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

# Optional per-role model overrides - see Section 2.1 below, and read its warning
# about model-swap cost before configuring anything other than matching models here.
agent_llms:
  planner:
    llm:
      model: "qwen3-coder:30b"
      base_url: "http://localhost:11434/v1"
  reviewer:
    llm:
      model: "qwen3-coder:30b"
      base_url: "http://localhost:11434/v1"
  run_verifier:
    llm:
      model: "qwen3-coder:30b"
      base_url: "http://localhost:11434/v1"
  skill_gap:
    llm:
      model: "qwen3-coder:30b"
      base_url: "http://localhost:11434/v1"
```

### 2.1 Per-Role Model Selection (`agent_llms`)
Planner, Architect, Developer, Reviewer, RunVerifier, and SkillGapAgent (skill-gap extraction and conflict-checking) don't have to share one model - each is independently configurable, with its own optional escalation chain.

**Read this before configuring anything other than matching models.** Kriya never explicitly loads or unloads a model - it just sends a `model` name in each request, and your local inference server (e.g. Ollama) decides whether that model is already resident or needs to be swapped in. Measured directly: alternating between three different models across one `generate` run (a leaner model for planning/review, a small model for structured utility calls, the primary model for Developer) made a real run **~3.8x slower** than using one model throughout - every model switch paid a full reload cost that dwarfed any inference-speed gain from the smaller models. The reverse is also true for free: two *consecutive* calls that happen to use the **same** model (e.g. Architect then Developer both on your primary model) pay no reload cost at all, because the model was already loaded - this happens automatically, with no configuration needed beyond picking matching model names.

**The safe default: leave `agent_llms` unset entirely, or point every role at the same model** (as in the example above) - either way, there is never a reload, by construction. Only configure genuinely different models per role if you've verified your machine can keep all of them resident in memory simultaneously (check with `ollama ps` after a run - every configured model should still show as loaded, not evicted by the next one). If you haven't verified that, per-role tiering will very likely make things slower, not faster.

Every role in `agent_llms` is independently optional - `llm: null` (the default, i.e. just omitting the role entirely) means "use the primary `llm` block above," so a project that never touches `agent_llms` sees zero behavior change. **Developer is deliberately not configurable here** - it always uses the top-level `llm`/`llm_chain`, escalated by the existing quality-gate retry loop (a compile/test failure is a fundamentally different signal than a call-level failure, so it keeps its own separate mechanism).

Each role also gets its own optional `llm_chain` - a list of fallback models tried in order if the role's own model fails, independent of Developer's chain:
```yaml
agent_llms:
  planner:
    llm:
      model: "qwen3-coder:30b"
      base_url: "http://localhost:11434/v1"
    llm_chain:
      - model: "qwen3:8b"          # only reached if the primary call itself fails
        base_url: "http://localhost:11434/v1"
```
What counts as "failure" differs by role, deliberately conservative so a legitimately short-but-correct response is never wrongly retried:
- **Planner, Architect, Reviewer** (free text): only a hard call failure - connection error, timeout, HTTP error, an `local_only` egress block. A brief plan or review is never retried just for being brief.
- **RunVerifier, SkillGapAgent** (JSON-mode): the same hard-call-failure signal, plus an unparseable response - if the first model doesn't even return valid JSON, the next candidate is tried before falling back to that role's existing safe-default behavior.

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

#### Resuming an interrupted run
`generate` (and `fix`, below) checkpoint after each stage - Plan, Design, and Developer output that's already passed Quality Gates - to `.kriya/checkpoints/` in your workspace. If a run gets killed or crashes partway through, re-run the *exact same command* (same goal, same workspace, same config) with `--resume` to pick up the most recent checkpoint, or `--resume-id <id>` for a specific one (the `id` is printed if the run finishes without quality gates passing):
```bash
kriya -c kriya.yaml generate "Create a Spring-XML Java 17 app running Ignite 2.18.0" --resume
```
This is opt-in only - Kriya never guesses that you're resuming from goal text alone. It's also strict: if anything about the workspace (a new commit, uncommitted changes), the config, or the goal text has changed since the checkpoint was saved, Kriya refuses to resume and starts over instead, with a warning explaining why. A checkpoint is deleted the moment its run finishes normally (success or an explicit rejection at the approval gate) - it only ever survives a kill/crash. Requires the workspace to be a git repository.

### 3.5 Fix Bugs (`fix`)
Locate and repair bugs in your project using reproduced test outputs or stack traces:
```bash
# Fix an issue by passing the error log string directly
kriya -c kriya.yaml fix -e "SyntaxError: invalid syntax in App.java line 12"

# Or pipe build output directly into the fix command
mvn clean compile | kriya -c kriya.yaml fix
```
`fix` supports the same `--resume`/`--resume-id` checkpoint resume as `generate` (above) - re-run with the same `-e`/piped error text plus `--resume` after a crash.

### 3.6 Interactive Session (`repl`)
Instead of restarting the CLI for every command, start a session and issue several in a row. On a real terminal, this is now the default for bare invocation - no subcommand needed:
```bash
kriya -c kriya.yaml
# equivalent, if you prefer to be explicit (e.g. in a script/alias):
kriya -c kriya.yaml repl
```
Bare invocation only starts the session when stdin is an actual interactive terminal - piped input or a non-interactive context (a script, CI) gets today's usual help text instead, so nothing hangs waiting on input by accident. Use `kriya repl` explicitly if you want to be unambiguous either way.

Inside the session, type commands exactly as you would after `kriya` on the command line, just without `kriya` itself and without repeating `-c kriya.yaml` (it's captured once at startup and applied to every command automatically, unless a line supplies its own `-c`/`--config`):
```
╭─ kriya
╰─> generate "add a health check endpoint" -y
╭─ kriya
╰─> ask "how does the retry loop work?"
```
Type `/` to see every command Kriya supports, filtered live as you keep typing (e.g. `/gen` narrows to `generate`) - selecting one replaces what you typed with the bare command name, ready to add arguments. A few session-only commands are always available: `/help`, `/clear`, and `/exit`/`/quit` (Ctrl-D also works) to end the session.

This is deliberately a thin wrapper, not a new command language: every line dispatches into the exact same command group the regular one-shot CLI uses, so there's nothing REPL-specific to learn beyond what's documented in this guide already, and no risk of the session's behavior drifting from `kriya <command>` run standalone.

#### 3.6.1 Natural-Language Routing (optional, off by default)

Instead of typing an explicit command, you can type what you want in plain English and Kriya will figure out which command to run. Off by default - turn it on in your config:
```yaml
routing:
  enabled: true
```
This needs its own embedding model pulled (separate from whatever `embedding.model` you use for code search - short natural-language phrases and long-form code retrieval are different jobs, and the packaged default embedding model scored meaningfully worse at this specific task in testing):
```bash
ollama pull embeddinggemma
```
Once enabled:
```
╭─ kriya
╰─> why is this test flaky
-> routed to: ask
...
╭─ kriya
╰─> add a health check endpoint
-> routed to: generate
...
```
If Kriya can't tell between two commands, it asks instead of guessing:
```
╭─ kriya
╰─> explain why this test keeps failing
Not sure which you meant:
  [1] fix        repair a specific error, bug, or failing test
  [2] ask        answer a question about how the repo works
Pick a number, or press Enter to cancel:
```
And if what you typed isn't something Kriya does (installing packages, deploying, git operations, and similar are explicitly out of scope - Kriya only writes/edits files inside a reviewable, human-approved change), it says so rather than guessing at the closest command:
```
╭─ kriya
╰─> install express and add it to package.json
I don't think that's something I can do - I write/fix/review/analyze code
and manage skills, but I don't run commands, install packages, or touch
live infrastructure. Type /help to see what I can do.
```
An explicit command you type always takes priority over routing - `generate "..."` still dispatches directly, with zero LLM/embedding overhead, exactly as if routing were off.

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

**Per-rule tracking**: the `verified` flag above is skill-level, but a passing run usually only exercises a handful of a skill's actual rules - marking the whole skill verified on that basis is coarser than it sounds. Kriya separately tracks each *individual* rule extracted from a skill gap or live lookup (Section 3.4 / Section 4.6) as unverified until a passing run's context specifically includes it. `kriya skills show <skill_name>` flags these inline:
```
Rules:
  - Always print output prefixed with [WIDGET].
  - Use WIDGET_CONSTANT = 999 as the magic widget constant.  [unverified]
```
This only applies to rules extracted since this tracking existed - pre-existing rules.txt content (including anything already in your skills before this feature) has no recorded provenance and is treated as already-trusted, not retroactively flagged. Generation prompts show unverified rules in a separate section labeled "use with appropriate caution" so the model has the same signal a human reviewing `kriya skills show` would.

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
Shows the 20 most recent runs by default (oldest hidden runs are counted in a trailing "Showing N of M" note, not silently dropped). Pass `-n/--limit <count>` to change how many are shown, or `--all` to print every recorded run.

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

**A third trigger, inside the Developer retry loop**: with the same `autonomy.web_lookup_enabled`/`search.base_url` switches on, a compile or Runtime Verification failure that repeats *identically* on a second consecutive retry attempt also triggers a lookup - a repeated failure suggests the model isn't self-correcting on its own. This one is silent (no batch confirmation, nothing written to a skill) since it's folding a hint into an already-fully-automated retry, not asking you to trust something new. It only searches for well-known tool/plugin/library coordinates found in the error text itself, never the error or stack trace as a whole. Real testing found this genuinely helps for actual unfamiliar-library knowledge gaps, but many repeated retry failures turn out to be the model losing track of something across a large multi-file response rather than missing information - this feature doesn't fix that class of failure, only the knowledge-gap class.

### 4.7 Targeted Single-File Retry
No configuration needed - this is always on. When a Quality Gates failure (compile or Runtime Verification) names one of the files Kriya already wrote, the next retry focuses on fixing just that file instead of regenerating your entire project again - the target file is shown to the model as the thing to fix, every other file is included as reference material so nothing else gets accidentally rewritten (though the model can still touch another file if the fix genuinely needs it, e.g. a missing import that also needs a new dependency added). This runs on its own budget (3 attempts) separate from the normal retry count, and always uses your primary model - never the fallback chain, since a model swap costs real time and the whole point of a targeted retry is to be fast.

If a failure doesn't clearly point at one of your files (a bare exit code, a build-tool configuration error with no source file involved), Kriya falls back to a normal full-file-set retry - targeting is a bonus when it can confidently narrow the fix, never a guess.

### 4.8 Completeness Prevention & Missing-File Recovery
No configuration needed - this is always on. Before the Developer generates anything, Kriya scans the Architect's design for the files it calls for and hands the Developer an explicit "Required files" checklist as part of the task description - not just a check applied after the fact. If a required file is still missing once generation finishes, the next retry asks specifically for that missing file (with the rest of your codebase shown as reference), instead of either silently accepting an incomplete result or regenerating everything from scratch. This shares Targeted Single-File Retry's budget (3 attempts) and never escalates models, for the same reasons.

### 4.9 Working on an Uncommitted/In-Progress Project
No configuration needed. The Developer & Quality Gates sandbox (`.kriya/worktree`) is synced with whatever is actually on disk in your workspace before every run - including uncommitted changes and new, not-yet-`git add`ed files - not just your last commit. You don't need to commit in-progress work before running `kriya generate`; a goal that builds on or preserves existing (even uncommitted) code sees it correctly either way.

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

---

## 8. Appendix: What It Actually Took to Get Kriya to Build This Reliably

This section is not a feature reference - it's a case study, kept honest and specific on purpose, meant to calibrate what to expect from Kriya and clarify what your own role is in making it reliable for the technologies you actually use.

### 8.1 The scenario

A single, genuinely real-world goal: a standalone Java 17 Maven application combining an embedded Apache Qpid Broker-J broker, an embedded Apache Ignite node, and Spring XML-wired JMS messaging between them - not a toy "hello world," but the kind of multi-technology integration a production task actually looks like. It was built up incrementally as three milestones (broker-only, cache-only, then wiring both together), specifically so each mechanism below could be tested and fixed in isolation before combining them.

**Milestone 1 (broker-only) took 9 live attempts to pass.** Milestones 2 and 3 each passed on the *first* attempt, zero retries, once the lessons from Milestone 1 (and a few more found along the way) were written into skill content and Kriya's own code. That gap - 9 attempts vs. 0 - is the entire point of this section.

### 8.2 What actually made the difference

None of the fixes were about the LLM "trying harder." Every one was either (a) real, previously-unverified/wrong information sitting in a skill's `rules.txt`/`examples/`, or (b) a real gap in Kriya's own workflow code:

- **Skill content bugs**: an exec-maven-plugin configuration pattern (`<mainClass>+<jvmArguments>`) that looked plausible, ran without a hard error, and was wrong - `jvmArguments` was never a real parameter for that goal, confirmed only by reading the actual plugin's own descriptor. A Qpid Broker-J internal default (`initialConfigurationLocation`'s hardcoded `classpath:system.properties`) that fails specifically under `exec:java`'s classloader, found via bytecode inspection, not documentation. Maven's `-q` flag silently dropping SLF4J logger output under `exec:java`, found by direct A/B comparison.
- **Workflow code bugs**: the Developer wasn't told the Architect's required-file list *before* generating, only checked against afterward, so a required file could simply be missing. A skill's overly-generic tags (`java`, `maven`, `spring`) caused it to activate on unrelated goals, once causing real skill-content cross-contamination. The sandbox used for compiling/testing generated code never reflected uncommitted work already in the workspace - the normal state of an in-progress project.

**The common thread**: every one of these was found by actually running the thing, not by reading the generated code and judging it plausible. A `PrivilegedActionException` or a silently-dropped log line doesn't show up in a code review - it shows up when you run `mvn compile exec:java` for real and watch it fail.

### 8.3 Your role: skills are the compounding asset

Kriya's autonomous mechanisms - Skill Gap Detection, Live Lookup, Targeted Single-File Retry, Completeness Prevention & Recovery - exist to make a *first* encounter with an unfamiliar technology survivable without you sitting there debugging it turn by turn. They reduce the pain. They do not eliminate the need for someone to have gone through the hard version once, correctly, and written down what they learned. That's what a skill's `rules.txt` and `examples/` actually are: a compounding memory of mistakes already made, so the next `generate` run against the same technology doesn't have to make them again.

Concretely, this means:

1. **For any technology or pattern your team uses repeatedly, invest in curating its skill** - even just capturing your first hard-won working example into `examples/` and the specific gotchas you hit into `rules.txt`. This is the direct cause of the 9-attempts-to-0-retries difference above. A skill with real, verified content is worth more than any amount of prompt tuning.
2. **When Kriya's Skill Gap Detection asks you for a reference, a real working example beats a documentation link.** Live Lookup genuinely struggles to extract anything usable from a generic doc page (observed repeatedly during this validation - "tried 2 reference(s) but none contained anything usable"); an actual verified `pom.xml` or class file is unambiguous and directly reusable.
3. **Kriya's own automatic learning is helpful but not infallible - review it occasionally.** Rules extracted from a skill gap or live lookup are written automatically; this validation surfaced real (and now-fixed) cases of near-duplicate rules accumulating and, before the fix, an extraction that silently overwrote a previously-curated example. `kriya skills show <skill_name>` and the `[unverified]` per-rule markers exist precisely so you can spot-check what's been auto-added.
4. **A single passing run doesn't prove everything it looks like it proves.** The `jvmArguments` mistake above was "verified" by a real, successful live test - that test simply never exercised the one thing that was actually broken, because the app under test didn't need it. Treat "verified" as "verified for what was actually tested," and be willing to dig one level deeper (the tool's own source or spec, not just another behavioral test) when something doesn't add up.
5. **Expect to be asked, and expect that to be normal, not a failure.** A Skill Gap or Skill Conflict prompt during `generate` is Kriya correctly recognizing the edge of its own verified knowledge, not a bug. The goal isn't zero questions - it's that the same question is never asked twice for the same fact.

The practical takeaway: Kriya is most reliable exactly where you or your team have already paid down the "first encounter" cost into a skill. For a brand-new, uncurated technology pairing, expect something closer to Milestone 1's experience than Milestone 3's - and treat that first hard session as the investment that makes every future run against the same stack look like Milestone 3 instead.
