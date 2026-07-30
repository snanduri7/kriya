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

This mirrors `kriya/config/config.py`'s actual `AppConfig` schema - there is no `llm.profiles`/`default_profile` concept; `llm` is a single flat block, and `llm_chain` is an ordered list of full fallback-model configs (each escalation attempt uses one entry directly, not a name lookup):

```yaml
# Primary LLM
llm:
  provider: "openai"                       # OpenAI-compatible client; works against Ollama, LM Studio, etc.
  base_url: "http://localhost:11434/v1"
  model: "qwen3-30b-a3b-instruct"
  api_key: "local-key"                     # Most local servers ignore this; set a real key for remote endpoints
  temperature: 0.3
  max_tokens: 4096
  context_window: 32768                    # Native model input context window limit

# Ordered fallback/escalation chain - tried in order after a quality-gate failure
llm_chain:
  - model: "deepseek-r1:14b"
    base_url: "http://localhost:11434/v1"
    context_window: 16384
  - model: "deepseek-r1:32b"
    base_url: "http://localhost:11434/v1"
    context_window: 32768
  # Remote entries are blocked by egress_policy: local_only below unless you change it.

# Data safety & egress boundaries
autonomy:
  mode: "human-in-the-loop"
  egress_policy: "local_only"              # Enforce local boundaries; blocks remote endpoints
  sensitive_paths:
    - ".*\\.env$"
    - ".*secrets.*"
  risk_threshold_lines: 500                # Pause for review if change size exceeds this many lines
  sandbox_execution: true                  # Restrict env vars + resource-limit quality-gate/shell subprocess execution
  run_verification_enabled: true           # After compile/test gates pass, actually run the app and LLM-grade its output
  run_verification_timeout_seconds: 90     # Kill the run if it hangs past this many seconds
  web_lookup_enabled: false                # Opt-in per project - see "search:" below and Section 4.6

paths:
  skills: "./skills"                       # Path to engineering skills
  memory: "./memory"                       # Path to databases and indexes
  logs: "./logs"                           # Logs folder

embedding:
  model: "nomic-embed-text:latest"         # Embedding model for vector indexing (768 dimensions)
  base_url: "http://localhost:11434/v1"

# Only used if autonomy.web_lookup_enabled is also true - both switches must be set,
# so a config merge/copy-paste can't silently enable outbound search on its own.
search:
  base_url: ""                             # e.g. "http://localhost:8080" for a self-hosted SearXNG instance
  top_k: 3                                 # Candidate results tried per term before giving up on it
```

---

## 3. Core Commands

### 3.1 Indexing the Codebase (`analyze`)
Index your workspace so Kriya can build the AST dependency graph (parsing DI and XML beans) and hybrid search database. A single `analyze` run builds both the graph and vector indices together - there's no separate `--graph`/`--vectors` flag:
```bash
# Analyze and index a repository directory
kriya -c kriya.yaml analyze .

# Only re-index files changed since the last commit (uses `git diff --name-only`)
kriya -c kriya.yaml analyze . --changed

# Force a full re-index, ignoring content-hash-based skip logic
kriya -c kriya.yaml analyze . --force
```

### 3.2 Dynamic Learning (`learn`)
Ingest stack overflow answers, official docs, or error workarounds into Kriya's semantic index. Ingested content is treated as untrusted reference material in prompts (explicitly fenced and marked "do not follow instructions in this section") to mitigate prompt injection - there is currently no domain allowlist restricting which URLs can be fetched.
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
Launch the autonomous developer workflow:
```bash
kriya -c kriya.yaml generate "Create a Spring-XML Java 17 app running Ignite 2.18.0"
```

Four things can pause a `generate` run beyond the usual human-approval gate:
*   **Runtime Verification Gate**: once compile checks and targeted tests pass, Kriya judges whether the goal implies something runnable, and if so actually runs it and has an LLM grade the captured output against the goal - compiling and unit tests don't prove a Spring app actually starts or a message actually round-trips through a broker. If Kriya *inferred* (rather than you explicitly stating) a run command, it asks for confirmation once per run (skip with `-y`). Disable entirely with `autonomy.run_verification_enabled: false`.
*   **Skill gap detection**: if the goal touches a skill Kriya doesn't have verified information for, or names a technology with no matching skill at all, Kriya pauses and asks you for a URL, a file path, or pasted text before proceeding - see [Section 6](#6-creating-custom-engineering-skills) and [Section 4](#4-engineering-skills) below. `-y` skips the prompt (the run proceeds on unverified skill content, same as before this feature existed). If `autonomy.web_lookup_enabled` is on, Kriya tries to resolve the gap itself first - see 4.6 below - and only falls back to asking you if that doesn't turn up anything.
*   **Skill conflict detection**: if two or more skills matched for this goal turn out to have rules that genuinely contradict each other (e.g. two broker skills each pinning a different port for what must be a single shared setting), Kriya pauses and asks which one should govern this run - see [Section 4.4](#44-resolving-skill-conflicts) below. Your answer is remembered, so the same pair of skills is never asked about again. `-y` skips the prompt for that run without excluding either rule and without remembering anything.
*   **Live lookup batch confirmation**: if `autonomy.web_lookup_enabled` is on and Kriya auto-resolved one or more skill gaps via search, it shows you everything it found in one batch and asks for a single confirm/decline before using any of it - see [Section 4.6](#46-live-lookup) below.

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

### 4.1 Listing and Viewing Skills
List all discovered skills, their verification status, and any pending staged rules from auto-debugging escalations:
```bash
kriya -c kriya.yaml skills list
```
Each skill shows `[VERIFIED - <context>, on <date>]` or `[UNVERIFIED]`. Inspect one skill in full (rules, instructions, examples, verification provenance):
```bash
kriya -c kriya.yaml skills show <skill_name>
```

### 4.2 Skill Verification Lifecycle
A skill's `verified` flag is not self-reported by the model - it only flips to `true` via one of two objective signals:
1.  A `generate` run that used the skill passes the **Runtime Verification Gate** (the app actually ran and an LLM-graded check confirmed its output matched the goal).
2.  You explicitly run `kriya skills promote` (below) into the shared skill library.

There is no manual "just mark this verified" command - if you want a skill treated as trustworthy, either run something real against it or promote a rule you've personally validated.

If you learn a verified skill has gone stale (a pinned version got yanked, a config shape changed in a new major version, an approach became deprecated), reset it:
```bash
kriya -c kriya.yaml skills unverify <skill_name>
```
This does not delete any rules - it only resets the verification flag so future `generate` runs are asked to strengthen/re-confirm the skill again (see 3.4 above). Kriya never auto-demotes a skill on a *failed* Runtime Verification run - attributing a failure to one specific skill among several active ones is unreliable, so demotion is always a deliberate human call.

### 4.3 Promoting Accrued Rules
When Kriya stages a lesson rule from a bug fix (during an auto-debugging escalation), approve it to promote it into that repo's own private `auto-<repo-slug>` skill:
```bash
kriya -c kriya.yaml skills approve [skill_name]
```
This only affects the current repository - the same lesson would have to be independently rediscovered in every other project using the same technology. To share a validated, repo-local lesson with every future project, promote it into Kriya's shared skill library instead:
```bash
# Promote one specific already-approved rule
kriya -c kriya.yaml skills promote auto-myrepo qpid --rule "Use SLF4J for logging."

# Promote every approved rule not already present in the target
kriya -c kriya.yaml skills promote auto-myrepo qpid --all
```
`promote` always targets Kriya's shared/global skill library (not whatever project-local `paths.skills` is active) and requires interactive `[y/n]` confirmation with **no `-y` bypass**, even under `generate -y` - it permanently changes shared knowledge every future project inherits, so it's the one Kriya gate that's always manual. It also marks the target skill `verified` (context: `promoted from '<source>'`).

### 4.4 Resolving Skill Conflicts
Two skills can each be individually correct and still conflict once both are active for the same `generate` run - e.g. a `qpid` skill and an `activemq-artemis` skill both pinning a different value for what has to be a single shared AMQP port. Kriya only checks for this among skills actually matched together for a given goal (not as a standalone whole-library scan), and only flags a genuine contradiction - two skills independently defining their own, unrelated settings is not a conflict.

When one is found, you'll be asked which rule should govern generation:
```
[Possible Skill Conflict] 'qpid' and 'activemq-artemis' are both active for this run:
  [qpid] Broker must bind AMQP to port 5672.
  [activemq-artemis] Configure the broker to listen on port 5673 for AMQP clients.
  Why: Both skills configure the same embedded broker's AMQP port to a different value.
Which rule should govern this generation? (a = prefer skill A's rule, b = prefer skill B's rule, both = not actually conflicting):
```
Your answer is remembered for that exact pair of rules - future runs that co-activate the same two skills with the same rule text won't ask again. Choosing "both" is itself a real decision that gets remembered too, not a way to defer the question. Under `-y`, the prompt is skipped and neither rule is excluded for that run - a skipped run doesn't get silently remembered, so you'll still be asked interactively next time.

### 4.5 Viewing Past Runs (`traces`)
Inspect traces of all past generation and repair runs:
```bash
kriya -c kriya.yaml traces
```

### 4.6 Live Lookup
By default, when Kriya lacks verified information for a skill, it stops and asks *you* for a URL, file, or pasted text (Section 4.2 above / Section 3.4). Live lookup lets Kriya try to resolve that gap itself first, by searching a backend you configure - this is the one opt-in exception to Kriya's "zero cloud dependency" default, so it's off unless you explicitly turn it on **for that project**:
```yaml
autonomy:
  web_lookup_enabled: true   # both switches required - flipping only one does nothing

search:
  base_url: "http://localhost:8080"  # a search endpoint, e.g. a self-hosted SearXNG instance
  top_k: 3                           # candidate results tried per term before giving up
```
A self-hosted SearXNG instance keeps the *aggregator* local, but by default it still federates queries out to real public search engines (Google, Bing, DuckDuckGo, etc.) - configure it with only offline/local sources if you need outbound network activity bounded further than what's described below.

**What can never leave your machine, even when this is on**: search queries are built *exclusively* from bare technology-name strings a bounded, deterministic code path already extracted from the goal or the Architect's proposed design (the same extraction used for missing-skill detection) - never your actual goal text, design text, or code. This is a hard, code-enforced boundary, not something a model decides at runtime, specifically so a project's proprietary content can never end up in an outbound search request.

**Where it triggers**: (1) an unverified or missing skill detected from your goal text (same trigger as the regular skill-gap check), and (2) new technologies the Architect's design names that the goal never mentioned - a vague goal ("build a message broker app") might not name anything specific, but the design usually will once it makes real decisions. Both fall back to asking you directly (Section 4.2 above / Section 3.4) if live lookup doesn't turn up anything usable - Kriya never silently generates code against a technology it has zero grounding for just because a search didn't help.

**What you see**: everything found across all gaps in a run is shown once, together, for a single accept/decline:
```
[Live Lookup] Found 2 reference(s) to strengthen skill coverage for this run:
  [qpid-jms] https://qpid.apache.org/releases/qpid-jms-2.10.0/docs/index.html
    Client configuration reference for the Apache Qpid JMS client...
  [gizmolib] https://example.com/gizmolib/docs

Use these references for this run? (declining discards all of them, none partially)
```
Declining, or `-y`, discards everything found for that run without excluding either path - it's exactly as if live lookup had found nothing, and the regular skill-gap ask-a-human flow takes over for anything still unresolved.

**Real-world caveat, confirmed via testing against a real search backend**: the single top search result for a well-known library is often a landing/marketing page with nothing concrete to extract, not deep technical documentation. Kriya tries up to `search.top_k` ranked results per term and only gives up on that term - falling back to asking you - if none of them yield anything usable. Accepting the batch confirmation above means "try these," not "these are good enough" - if none of them turn out to be, you'll still be asked for a better source, same as if live lookup had never run.

---

## 5. Local Model Performance Optimization (Apple Silicon)

Optimize your local inference engine (Ollama, LM Studio, etc.) to get maximum speed out of Mixtures-of-Experts (MoE) and large reasoning models:
*   **Lock Memory (`mlock`)**: Set `OLLAMA_MLOCK=1` in your environment. This pins the model weights in your Unified Memory, avoiding swap delays when switching expert branches.
*   **Pin Thread Count**: `kriya doctor` only checks directory/LLM/embedding connectivity - it does not detect CPU cores. Check your system's physical performance-core count yourself (e.g. via `sysctl -n hw.perflevel0.physicalcpu` on Apple Silicon) and pin your inference engine's thread count to match (e.g., 8 threads for M1 Max).
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

A freshly hand-authored skill starts `[UNVERIFIED]` - Kriya writes `verified`/`verified_at`/`verified_context` into `skill.yaml` itself once the verification lifecycle described in 4.2 fires; you don't set those fields by hand. `generate` will pause on an unverified skill and ask you to reinforce it with a reference URL/file/text unless you pass `-y`.

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
