# Kriya – Product Vision & Technical Specification

Version: 2.0
Status: Living Document
Focus: Local-First, Production-Grade Engineering Platform

---

# 1. Vision

Kriya is a production-grade AI Engineering Platform designed for absolute data privacy and control. It operates entirely offline, utilizing local Large Language Models (LLMs), tools, MCP servers, and multi-agent systems.

Kriya is not an AI chatbot.
Kriya is not a prompt wrapper.
Kriya is an extensible engineering platform capable of understanding complex repositories, planning architectural changes, orchestrating AI capabilities across multiple agents, executing local tools, and producing production-quality software. It augments engineering teams with reliable, private, and extensible AI capabilities.

---

# 2. Product Philosophy

Kriya follows five core principles:

1.  **Local-First by Default:** Zero data leaves the user's machine. All processing, model inference, and data storage occur locally.
2.  **Platform over Application:** Extensible architecture where every component is a replaceable plugin.
3.  **Configuration over Hardcoding:** Everything is defined via YAML or code to avoid vendor lock-in.
4.  **Repository-aware Generation:** Generation is contextually bound to the specific repository's architecture and patterns.
5.  **Production Quality over Toy Examples:** Generated code must compile, pass linters, and be secure, maintainable, and testable.

---

# 3. Primary Users & Use Cases

- **Software Engineers:** Build new features, debug, refactor, and understand legacy code.
- **Solution Architects:** Analyze architecture drift, generate boilerplate for new microservices, and document system design.
- **Technical Leads:** Enforce coding standards, automate code reviews, and onboard new developers.
- **DevOps Engineers:** Generate Infrastructure as Code (IaC) and CI/CD pipelines.
- **Enterprise Teams:** Work in air-gapped environments or highly regulated industries requiring data sovereignty.

---

# 4. Core Platform Goals

Kriya must be effective in two distinct modes:
1.  **Analysis Mode:** Deeply understand an existing repository (structure, dependencies, patterns, logic).
2.  **Creation Mode:** Scaffold new projects from scratch following best practices for the specific stack.

**Supported Stacks (Phase 1):**
- **JVM:** Java, Spring Boot, Spring XML Configuration, Maven/Gradle.
- **Dynamic:** Python, Ruby.
- **Infrastructure:** Docker, Kubernetes (via YAML generation).

---

# 5. Detailed Functional Architecture

## 5.1 Local LLM Abstraction Layer
Kriya is model-agnostic. It interfaces with any local inference server.
- **Support:** Ollama, LM Studio, llama.cpp, vLLM, or custom PyTorch models.
- **Strategy:** Implements a "Router" that decides context-window usage. For large repos, it uses a summarization model; for coding tasks, it routes to a coding-specific model (e.g., DeepSeek-Coder, CodeLlama).
- **Configuration:** `models.yaml` defines endpoint, context window, and prompt style.

## 5.2 Multi-Agent System (MAS)
Orchestrates specialized agents locally. Agents communicate via a local message bus.
- **Planner Agent:** Decomposes large tasks (e.g., "Refactor this service") into sub-tasks.
- **Architect Agent:** Analyzes `pom.xml`, `build.gradle`, or `requirements.txt` to understand structure.
- **Developer Agent:** Writes implementation code.
- **Reviewer Agent:** Reviews code against repo-specific linting rules.
- **Test Agent:** Generates unit/integration tests (JUnit, PyTest, RSpec).
- **Documentation Agent:** Generates OpenAPI, Javadoc, or READMEs.

## 5.3 Repository Understanding Engine (RAG 2.0)
Moves beyond simple vector search to a "Knowledge Graph" of the repository.
- **AST Parser:** Uses Tree-sitter to parse code into Abstract Syntax Trees.
- **Call Graph:** Maps function calls and dependencies.
- **Data Flow:** Tracks how data moves through the application.
- **Storage:** Stores metadata in a local vector database (ChromaDB / LanceDB) for semantic retrieval, and a Graph DB (Neo4j Local) for structural queries.

## 5.4 Tool Execution Framework
A safe, sandboxed execution environment for tools.
- **File System:** Read/Write with workspace path restrictions.
- **Shell:** Safely execute `mvn clean install`, `pip install`, `gem install`.
- **AST Manipulation:** Ability to rewrite code structurally (e.g., "Rename this method across 100 files").
- **MCP Integration:** Connect to local filesystem servers, Git servers, or Docker daemons via the Model Context Protocol (MCP).

## 5.5 Skill Library & Templates
Encapsulates expert knowledge for specific frameworks.
- **Spring Boot Skill:** Contains templates for Controllers, Services, Repositories, and XML configurations.
- **Python Skill:** Best practices for Django/FastAPI, pipenv/poetry usage.
- **Ruby Skill:** Rails conventions and RSpec patterns.
- **Execution:** Skills are loaded dynamically based on the detected repository context (e.g., `analyze` detects Spring Boot -> loads Spring Skill).

## 5.6 Memory Engine (Local)
Persists context across sessions.
- **Short Term:** Conversation context.
- **Long Term:** Stored "lessons learned" (e.g., "We don't use Lombok here").
- **Index:** Uses embedding models locally to retrieve relevant past decisions.

---

# 6. Workflow Execution Engine

A task goes through the following pipeline:

1.  **Validation:** Is the task valid? (e.g., "Cannot compile without JDK").
2.  **Context Injection:** Analyzes the repo for relevant files.
3.  **Planning:** Planner agent creates a `task.json` checklist.
4.  **Execution:** Developer agent writes code, Tool framework runs linters.
5.  **Review:** Reviewer agent checks if it matches the plan.
6.  **Post-Processing:** Tool framework formats code (Spotless, Black, Rubocop).

---

# 7. Configuration & Extensibility

## Plugin Architecture
Everything is a plugin:
- LLM Provider
- Repository Parser
- Tools
- MCP Clients

## Global Configuration (`kriya.yaml`)
```yaml
project:
  name: MyApp
  language: java
  build_tool: maven

llm:
  provider: ollama
  model: deepseek-coder:6.7b
  context_window: 8192

skills:
  enabled:
    - spring-boot
    - kafka

agents:
  max_iterations: 5
  temperature: 0.2

tools:
  - filesystem
  - shell
  - ast


8. Repository Analysis & Creation
8.1 Analyzing an Existing Repo
kriya analyze .

Reads pom.xml, application.properties, etc.

Parses Java/Python/Ruby sources into AST.

Builds dependency graph.

Outputs a repo_model.json that can be queried.

Capability: "Explain this 1000-line class" -> Agent uses AST to summarize logic.

Capability: "Add a new endpoint" -> Agent finds the correct controller path and adjusts the web.xml or annotations.

8.2 Creating from Scratch
kriya init

Interactive CLI asks for: Language, Framework, Build Tool.

Downloads the relevant Skill Template.

Populates templates with user inputs (Group ID, Artifact ID, etc.).

Generates the base structure and runs mvn compile to ensure it works.

9. Quality Gates (Local Execution)
Kriya ensures production readiness by executing the following against the generated code:

Static Analysis: Run SpotBugs, PMD, or SonarQube (scanner).

Testing: Run mvn test and ensure >80% coverage (configurable).

Formatting: Auto-format with prettier or spotless.

Security Scanning: Basic scan for secrets or unsafe syscalls.

10. CLI Commands (Expanded)
bash
kriya version
kriya doctor                  # Checks local env (Java, Python, Docker, LLM availability)
kriya model download codellama # Downloads the recommended model
kriya init                    # Creates a new project scaffold
kriya analyze .               # Builds the repository index
kriya generate               # Interactive generation "Write a REST controller"
kriya generate "Add OpenAPI spec" # Direct command
kriya review .               # Reviews all uncommitted changes
kriya test .                 # Generates missing tests
kriya doc .                  # Generates JavaDocs or README
kriya workflow               # Execute a multi-step plan
kriya memory list            # Show stored project memory
kriya tools exec -- "mvn clean compile" # Execute tools directly
11. Implementation Roadmap
Phase 1: Platform Foundation
Core Kernel & Plugin Registry

Configuration Engine (YAML parsing)

Shell Tool & File System Tool (Sandboxed)

Basic Java/Python AST Parser (Detection only)

CLI Framework Foundation

Phase 2: Engineering Foundation
LLM Abstraction Layer (Ollama/LM Studio support)

Prompt Engineering Suite (Templates for Spring/Java)

Repository Analyzer (Generates repo_model.json)

Spring Boot Skill Loader (Templates & Rules)

Quality Gate Execution (Compilation check)

Basic Generation Capabilities

Phase 3: Knowledge & Skills
Python & Ruby Skills

Advanced Skill Library Expansion

Memory Engine (Short & Long Term)

Template Management System

Vector Database Integration for RAG

Multi-Language AST Parsing (Tree-sitter)

Phase 4: Integration & Autonomy
MCP Client Integration (Filesystem, Git, Docker)

Multi-Agent Framework (Planner + Developer)

Graph Database Integration for Call Graphs

Tool Orchestration Engine

Repository Graph Construction

Code Rewriting Capabilities (AST Manipulation)

Spring XML Specific Support

Phase 5: Intelligence & Production Readiness
Full Multi-Agent Coordination (All agents collaborating)

Knowledge Graph Memory (Semantic + Structural)

Workflow Engine (Complex multi-step plans)

Advanced Repository Understanding (Data Flow, Architecture Detection)

Production Validations (Security scanning, performance analysis)

Enterprise Features (Audit logging, team collaboration support)

Self-Improvement Capabilities (Learning from user feedback)

Phase 6: The Intelligent Compiler - Long-Term Evolution
Kriya evolves into the "Intelligent Compiler" of the future, transcending traditional compilation to become a semantic and architectural guardian.

Capabilities:

Semantic Validation: Validates business logic against architectural constraints and best practices, not just syntax.

Predictive Analysis: Anticipates bugs, performance bottlenecks, and security vulnerabilities before they occur based on code patterns and historical data.

Autonomous Refactoring: Continuously improves code quality without manual intervention, suggesting and applying structural optimizations.

Cross-Repository Intelligence: Learns patterns across multiple projects while maintaining strict data privacy and localization.

Natural Language Engineering: Allows non-technical stakeholders to request features that get automatically translated into technical specifications and implementation plans.

Ecosystem Integration: Seamlessly works with existing CI/CD pipelines, monitoring tools, and cloud platforms while keeping all sensitive data local.

Architectural Governance: Enforces architectural decisions automatically, preventing drift from approved patterns.

Legacy Modernization: Automatically suggests and implements migration paths from legacy systems to modern architectures.

Core Philosophy: The Intelligent Compiler understands the "why" behind the code, enabling engineers to focus on solving complex business problems rather than wrestling with boilerplate, legacy system intricacies, or infrastructure concerns.

Kriya will not replace engineers but augment them with a reliable, private, and production-grade AI partner capable of handling the complexity of modern software systems while ensuring data sovereignty and enterprise-grade security.

12. Conclusion
Kriya represents a paradigm shift in how software engineering teams interact with AI. By combining local-first privacy, extensible architecture, and deep repository understanding, it provides a platform that grows with the organization's needs.

The phased approach ensures that value is delivered incrementally, starting with foundational capabilities and evolving toward the ambitious vision of the Intelligent Compiler. Each phase builds upon the previous, creating a robust, production-ready system that engineers can trust with their most critical work.

With Kriya, engineering teams gain a powerful ally that respects their privacy, understands their code, and helps them build better software faster.
