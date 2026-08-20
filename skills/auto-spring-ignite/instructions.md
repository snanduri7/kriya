## Coding Standards and Architecture

### Naming Conventions
- Package names should follow reverse domain name convention (e.g., `com.example`)
- Class names must use PascalCase
- File names should match the main class name

### Packaging
- Source code resides in `src/main/java`
- Resources are stored in `src/main/resources`
- Maven standard directory structure is used

### Imports
- Import statements should be organized alphabetically within sections
- Static imports should be used sparingly and only for utility methods

### Component Layout
- Main application class named `App` in root package
- Configuration files stored in `src/main/resources`
- Maven POM file at project root

### Architecture Rules
- Use Spring XML configuration for Ignite setup (`ignite-config.xml`)
- Follow standard Maven dependency management
- All Ignite operations should be wrapped in try-with-resources blocks

### Testing Frameworks
- No testing frameworks currently configured

### Dependencies
- Core Ignite dependencies are managed via Maven
- Uses `ignite-core` and `ignite-spring` artifacts
- Compiler target set to Java 17