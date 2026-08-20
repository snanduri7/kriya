## Coding Standards and Architecture Guidelines

### Naming Conventions
- Class names should follow PascalCase convention (e.g., `HelloWorld`)
- Package names should be lowercase and follow reverse domain naming (e.g., `com.example`)
- Property names should follow camelCase convention (e.g., `message`)

### Packaging Structure
- Source code should be organized under `src/main/java` directory
- Resource files should be placed under `src/main/resources` directory
- Maven standard project structure is used with `pom.xml` at the root

### Imports and Dependencies
- All dependencies are declared in `pom.xml` file
- Spring Framework artifacts are imported using Maven coordinates
- Main class should be specified in the Maven Shade Plugin configuration for executable JAR

### Component Layout
- XML configuration files should be placed in `src/main/resources` directory
- Beans are defined using standard Spring XML schema with proper namespace declarations
- Service classes should follow naming convention with 'Service' suffix if applicable

### Build Process
- Project uses Maven build system with `maven-shade-plugin` for creating executable JARs
- Java version 8 is targeted for compilation and runtime
- Build artifacts are placed in `target` directory