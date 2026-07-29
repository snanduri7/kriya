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

Create a `kriya.yaml` configuration file in your workspace to manage model settings, paths, and egress policies.

```yaml
# LLM Endpoint Profiles
llm:
  default_profile: "primary-moe"
  profiles:
    primary-moe:
      provider: "openai_compatible"
      base_url: "http://localhost:11434/v1"
      model: "qwen3-30b-a3b-instruct"      # Primary fast MoE model
      temperature: 0.3                     # Slightly higher temperature for MoE expert routing
      max_tokens: 4096
      context_window: 32768                # Native model input context window limit
    
    fallback-14b:
      provider: "openai_compatible"
      base_url: "http://localhost:11434/v1"
      model: "deepseek-r1:14b"
      temperature: 0.2
      max_tokens: 4096
      context_window: 16384
      
    fallback-32b:
      provider: "openai_compatible"
      base_url: "http://localhost:11434/v1"
      model: "deepseek-r1:32b"
      temperature: 0.2
      max_tokens: 4096
      context_window: 32768

    remote-deepseek:
      provider: "openai_compatible"
      base_url: "https://api.deepseek.com"
      model: "deepseek-reasoner"
      temperature: 0.2
      max_tokens: 4096
      context_window: 64000
      api_key_env: "DEEPSEEK_API_KEY"       # Read key from local environment variable

# Ordered Fallback Chain (Escalation Series)
llm_chain:
  - "fallback-14b"
  - "fallback-32b"
  # - "remote-deepseek"                    # Remote fallbacks block under local_only policy

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
  model: "nomic-embed-text:latest"         # Embedding model for vector indexing (768 dimensions)
  base_url: "http://localhost:11434/v1"
```

---

## 3. Core Commands

### 3.1 Indexing the Codebase (`analyze`)
Index your workspace so Kriya can build the AST dependency graph (parsing DI and XML beans) and hybrid search database:
```bash
# Build the AST dependency graph (specifying workspace root)
kriya -c kriya.yaml analyze . --graph

# Build/update the semantic vector index
kriya -c kriya.yaml analyze . --vectors
```

### 3.2 Dynamic Learning (`learn`)
Ingest stack overflow answers, official docs, or error workarounds into Kriya's semantic index. All ingested content is treated as untrusted reference material to prevent prompt injection.
```bash
# Ingest from a URL (constrained by domain allowlist)
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
Launch the autonomous developer workflow:
```bash
kriya -c kriya.yaml generate "Create a Spring-XML Java 17 app running Ignite 2.18.0"
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

### 4.1 Listing and Viewing Staged Rules
Verify all discovered skills and check for pending staged rules extracted during auto-debugging escalations:
```bash
kriya -c kriya.yaml skills list
```

### 4.2 Promoting Accrued Rules
When Kriya stages a lesson rule from a bug fix, approve it to promote it to active skill guidelines:
```bash
kriya -c kriya.yaml skills approve [skill_name]
```

### 4.3 Viewing Past Runs (`traces`)
Inspect traces of all past generation and repair runs:
```bash
kriya -c kriya.yaml traces
```

---

## 5. Local Model Performance Optimization (Apple Silicon)

Optimize your local inference engine (Ollama, LM Studio, etc.) to get maximum speed out of Mixtures-of-Experts (MoE) and large reasoning models:
*   **Lock Memory (`mlock`)**: Set `OLLAMA_MLOCK=1` in your environment. This pins the model weights in your Unified Memory, avoiding swap delays when switching expert branches.
*   **Pin Thread Count**: Run `kriya doctor` to detect your system's performance cores. Pin the CPU threads to **match your system's physical performance cores** (e.g., 8 threads for M1 Max).
*   **Configure Context Size (`num_ctx`)**: Ollama defaults to `num_ctx: 2048` or `4096`. You must explicitly configure `num_ctx` to match your model's native context window (e.g. `32768`) in Ollama API calls or your Modelfile, otherwise context chunks will be silently truncated.

---

## 6. Creating Custom Engineering Skills

To guide Kriya's generation and debugging offline and prevent local models from hallucinating dependencies or APIs, you can create custom engineering skills.

### 6.1 Skill Directory Structure
Create a subfolder in your `paths.skills` directory (e.g., `skills/activemq-artemis/`):

```
skills/activemq-artemis/
├── skill.yaml            # YAML Metadata (name, tags, description)
├── rules.txt             # Lint/architectural rules, one per line
├── instructions.md       # Detailed guide for code structure
└── examples/             # Reference files that Developer Agent can match
    └── BrokerServer.java
```

### 6.2 Example Configs
- **`skill.yaml`**:
  ```yaml
  name: activemq-artemis
  description: Embedded ActiveMQ Artemis AMQP Broker setup instructions.
  tags: [artemis, activemq, broker, amqp]
  ```
- **`rules.txt`**:
  ```txt
  Use org.apache.activemq:artemis-server and artemis-amqp-protocol dependencies (version 2.31.2).
  Do not use artemis-core-server; use artemis-server instead.
  ```

---

## 7. How to Run the Apache Ignite + Qpid AMQP Messaging Application

To execute the generated Spring XML-based Apache Ignite and embedded ActiveMQ Artemis AMQP application:

### Step 1: Start the Embedded Broker Server
In your first terminal session:
```bash
# Build the project classes and download dependencies
mvn clean compile

# Build a text file with the project classpath dependencies
mvn dependency:build-classpath -Dmdep.outputFile=cp.txt

# Run the Standalone Embedded AMQP Broker
java -cp target/classes:$(cat cp.txt) com.example.BrokerServer
```

### Step 2: Start the Client Application
In a separate terminal session, run the client application which connects to the broker, sends a test message, retrieves it, and caches it in Ignite:
```bash
mvn compile exec:exec
```
