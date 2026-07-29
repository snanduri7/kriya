# Kriya: User Guide

This guide describes how to install, configure, optimize, and use Kriya for codebase Q&A, indexing, learning, and code generation.

---

## 1. Installation & Setup

1.  **Clone the Repository**:
    ```bash
    git clone https://github.com/your-repo/kriya.git
    cd kriya
    ```
2.  **Create a Virtual Environment & Install**:
    ```bash
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -e .
    ```
3.  **Ensure local LLM server is running**:
    *   Make sure Ollama, LM Studio, or your local inference engine is active and running.

---

## 2. Configuration (`kriya.yaml`)

Create a `kriya.yaml` configuration file in your workspace to manage model settings, paths, and policies.

```yaml
llm:
  provider: "openai"
  base_url: "http://localhost:11434/v1"
  model: "qwen3-30b-a3b-instruct"      # Primary fast MoE model
  temperature: 0.3                     # Optimized for MoE routing
  max_tokens: 4096
  knowledge_cutoff: "2023-12-01"       # Estimated model cutoff date
  knowledge_cutoff_confidence: "estimated"

knowledge:
  check_enabled: true                  # Toggle KnowledgeGuard verification
  offline_mode: false                  # Force using the cache and skip live HTTP queries

# Ordered Fallback Chain (Escalation Series)
llm_chain:
  - model: "deepseek-r1:14b"           # Fallback Tier 1: Small Reasoner
    base_url: "http://localhost:11434/v1"
  - model: "deepseek-r1:32b"           # Fallback Tier 2: Medium Reasoner
    base_url: "http://localhost:11434/v1"

# Data safety & egress boundaries
autonomy:
  mode: "human-in-the-loop"
  egress_policy: "local_only"              # Enforce local boundaries; blocks remote endpoints
  sensitive_paths:
    - ".*\\.env$"
    - ".*secrets.*"
  risk_threshold_lines: 100                # Pause for review if change size exceeds 100 lines

paths:
  skills: "./skills"                       # Path to engineering skills
  memory: "./memory"                       # Path to databases and indexes
  logs: "./logs"                           # Logs folder

embedding:
  model: "nomic-embed-text:latest"         # Embedding model for vector indexing
  base_url: "http://localhost:11434/v1"
```

---

## 3. Core Commands

### 3.1 Indexing the Codebase (`analyze`)
Index your workspace so Kriya can build the AST dependency graph and hybrid search database:
```bash
# Analyze and index the current repository
kriya -c kriya.yaml analyze .

# Only index files changed in git
kriya -c kriya.yaml analyze . --changed

# Force complete re-indexing
kriya -c kriya.yaml analyze . --force
```

### 3.2 Dynamic Learning (`learn`)
Ingest stack overflow answers, official docs, or error workarounds into Kriya's semantic index:
```bash
# Ingest from a URL
kriya -c kriya.yaml learn -u "https://ignite.apache.org/docs/latest/setup"

# Ingest from raw text
kriya -c kriya.yaml learn -t "In Apache Ignite, getOrCreateCache must be called on the Ignite instance, not on Ignition."
```

### 3.3 Ask Q&A (`ask`)
Query Kriya about structural or architectural questions in your repository:
```bash
kriya -c kriya.yaml ask "How is the Ignite server bean configured in Spring XML?"
```

### 3.4 Generate Code (`generate`)
Launch the autonomous developer workflow. To prevent shell escaping issues, pass prompts via a markdown/text file or redirect stdin:
```bash
# Run with inline description (for simple prompts)
kriya -c kriya.yaml generate "Create Apache Ignite 2.18 app"

# Run by reading the prompt from a text/markdown file (Recommended for complex prompts)
kriya -c kriya.yaml generate -f prompt.md

# Run via standard input (stdin) redirection
cat prompt.md | kriya -c kriya.yaml generate
```

### 3.5 Fix Bugs (`fix`)
Locate and repair bugs in your project using reproduced test outputs or stack traces:
```bash
# Fix an issue by passing the error log string directly
kriya -c kriya.yaml fix -e "SyntaxError: invalid syntax in App.java line 12"

# Or pipe build output directly into the fix command
mvn clean compile | kriya -c kriya.yaml fix
```

---

## 4. Engineering Skills

### 4.1 Listing and Creating Skills
Verify discovered skills, create custom skills, or promote staged rules:
```bash
# List all skills loaded
kriya -c kriya.yaml skills list

# Create a new custom skill skeleton
kriya -c kriya.yaml skills create [skill_name]

# Promote accrued rules to active skills
kriya -c kriya.yaml skills approve [skill_name]
```

### 4.2 Optimizing Prompts
Generate optimized, detailed developer prompts for Kriya using high-level requirements:
```bash
kriya -c kriya.yaml prompt generate "Spring XML App with Ignite 2.18" > prompt.md
```

### 4.3 Viewing Past Runs (`traces`)
Inspect traces of all past generation and repair runs:
```bash
kriya -c kriya.yaml traces
```

---

## 5. Local Model Performance Optimization (Apple Silicon)

*   **Lock Memory (`mlock`)**: Set `OLLAMA_MLOCK=1` in your environment to pin the model weights in your Unified Memory.
*   **Pin Thread Count**: Run `kriya doctor` to detect physical performance cores. Pin the CPU threads to match this count.
*   **Configure Context Size (`num_ctx`)**: Ensure the context size parameters in your inference server match your configured LLM settings.
