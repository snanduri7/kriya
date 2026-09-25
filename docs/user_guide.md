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
  context_window: 32768                    # Native model input context window limit, used for context-budget
                                            # allocation - not sent to the server.
  extra_body:                              # Passed straight through into the completion request body - backend-
    options:                               # specific sampling knobs live here. For Ollama: num_ctx/top_p/top_k.
      num_ctx: 32768                       # For a reasoning-capable model served through Ollama's OpenAI-compat
      top_p: 0.8                           # surface, this is also where reasoning_effort/presence_penalty go
      top_k: 20                            # (e.g. {"reasoning_effort": "none"} to force a thinking model into
                                            # non-thinking mode - see docs/kriya_backlog_and_lessons.md).
  reasoning: false                         # Marks this model as reasoning-capable (raises the max_tokens floor,
                                            # strips inline <think> tags from the response). Leave false for a
                                            # non-thinking model.
  retry_temperature: 0.1                   # Optional, unset by default. Overrides temperature ONLY for a
                                            # Developer completion that's fixing a real prior error (see §4.7) -
                                            # a cited study found code-gen success rate drops as temperature
                                            # rises even at the low end, so lower (not higher) helps a retry.
  reviewer_temperature: 0.7                # Optional, unset by default. Overrides temperature ONLY for the
                                            # ReviewerAgent's own completion call, independent of `temperature`
                                            # above - added after a live incident where a Reviewer call at low
                                            # temperature entered a verbatim repetition loop (the same MoE-
                                            # repetition failure class `temperature` itself exists to avoid,
                                            # just for a call site a single global value doesn't reach).
  capabilities:                            # Measured local-model protocol capabilities, EXPLICIT configuration -
    native_tool_calls: true                # never inferred from API response shape. Defaults shown are correct
    json_mode: true                        # for most modern OpenAI-compatible local servers (Ollama, LM Studio).
    reliable_multiline_json: false         # Native tools fail closed when disabled (never silently degrade to a
    streaming: true                        # different protocol); oversized/malformed tool call arguments are
    max_tool_argument_chars: 8192          # rejected rather than truncated. See §4.13 below.
    preferred_edit_protocol: "small_native_tools"

# Ordered fallback/escalation chain - tried in order after a quality-gate failure
llm_chain:
  - model: "deepseek-r1:14b"
    base_url: "http://localhost:11434/v1"
    context_window: 16384
    reasoning: false                       # Explicit, not incidental - escalating to a reasoning model on a
                                            # retry was measured at 13-85x the latency of the primary model for
                                            # zero correctness benefit on this failure class (fact-recall-shaped
                                            # compile/test fixes). Deterministic grounding (LSP) helps here, not
                                            # more reasoning - see docs/kriya_backlog_and_lessons.md.
  - model: "deepseek-r1:32b"
    base_url: "http://localhost:11434/v1"
    context_window: 32768
    reasoning: false
  # Remote entries are blocked by egress_policy: local_only below unless you change it.

# Data safety & egress boundaries
autonomy:
  mode: "human-in-the-loop"                # Any other value behaves as "guardrails" - approval then only
                                            # triggers on a sensitive-path match or risk_threshold_lines, not
                                            # on every change.
  egress_policy: "local_only"              # Enforce local boundaries; blocks remote endpoints
  sensitive_paths:                         # ADDED to, never replaces, a baseline force-appended regardless of
    - ".*\\.env$"                          # config: .env, secrets, .github/workflows/, Jenkinsfile, credentials,
    - ".*secrets.*"                        # password (kriya/config/config.py::load_config()) - you cannot
                                            # accidentally disable protection for those by overriding this list.
  risk_threshold_lines: 500                # Pause for review if change size exceeds this many lines
  sandbox_execution: true                  # Restrict env vars + resource-limit quality-gate/shell subprocess execution
  run_verification_enabled: true           # After compile/test gates pass, actually run the app and LLM-grade its output
  run_verification_timeout_seconds: 90     # Kill the run if it hangs past this many seconds
  spec_compliance_enabled: true            # After every other gate passes, an LLM checks whether the goal's
                                            # LITERALLY named requirements (an exact field/method/class name, type,
                                            # or constant) actually appear in the generated code - catches a
                                            # syntactically-valid, semantically-noncompliant result compile/test/
                                            # run-verification can't. Defaults false at the pydantic-model level
                                            # (test-suite blast-radius reasons, not a trust concern) but true in
                                            # the packaged default_config.yaml - validated live, 2026-08-22, against
                                            # the actual incident that motivated building it.
  web_lookup_enabled: false                # Opt-in per project - see "search:" below and Section 4.6
  self_correction_loop_enabled: false      # Opt-in: a bounded native-tool-calling micro-loop on a compile
                                            # failure, before falling back to full-file regeneration.
  best_of_n_first_attempt: 1               # >1 tries that many independent candidates on the very first attempt
                                            # only (never on retries, which already have real error grounding).
  generation_time_budget_seconds: null     # Set to the harness deadline (or slightly below); null is unbounded
  generation_gate_reserve_seconds: 120     # Keep enough time for authoritative quality gates
  generation_seconds_per_file_estimate: 90 # Replaced by observed per-file timing during the run

paths:
  skills: "./skills"                       # Path to engineering skills
  memory: "./memory"                       # Path to databases and indexes
  state: null                              # persistent state (traces.db), see 2.0b; file logs: see 2.0a

# Set both false for a reproducible plain-Kriya run. paths.skills above
# remains the one explicit project skill directory.
skills:
  load_global: true
  load_cwd: true

embedding:
  model: "nomic-embed-text:latest"         # Embedding model for vector indexing (768 dimensions) - no `provider`
  base_url: "http://localhost:11434/v1"    # field; embedding goes through the same OpenAI-compatible client as llm.

# Only used if autonomy.web_lookup_enabled is also true - both switches must be set,
# so a config merge/copy-paste can't silently enable outbound search on its own.
search:
  base_url: ""                             # e.g. "http://localhost:8080" for a self-hosted SearXNG instance
  top_k: 3                                 # Candidate results tried per term before giving up on it
  public_terms: []                         # Extra identifiers explicitly safe for unattended lookup

# Natural-language routing inside `kriya repl` (§3.6.1 below) - on by default. Needs
# `ollama pull embeddinggemma` in addition to whatever embedding.model above uses -
# a deliberately separate model, tuned for short-phrase intent classification rather
# than the long-form retrieval embedding.model is tuned for.
routing:
  enabled: true
  embed_model: "embeddinggemma:latest"
  reject_threshold: 0.3                    # Below this cosine similarity to every command's exemplars, treat
                                            # the input as out of scope even if the LLM gate said otherwise.
  ask_margin: 0.05                         # Top-2 candidate commands within this similarity margin -> ask which
                                            # one instead of guessing.

# Stage 0 KnowledgeGuard - scans a goal's text for library/version mentions that
# postdate training_cutoff and can pause the run for explicit confirmation.
knowledge:
  training_cutoff: "2023-12-01"
  check_enabled: true
  offline_mode: false

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

### 2.0a Where logs go (`logging`)
`kriya.log` and the per-run logs never land in the directory you run Kriya from. Run history is separate:
see 2.0b.

```yaml
logging:
  level: "INFO"
  directory: null          # null = ~/.kriya/logs; otherwise an absolute path (a relative one is refused)
  file_enabled: true       # application log: <directory>/kriya.log
  run_file_enabled: true   # per-run log: <directory>/runs/<run_id>/kriya.log
```

- **Which directory wins:** the `KRIYA_LOG_DIR` environment variable, then `logging.directory`, then `~/.kriya/logs`.
- **A repository cannot redirect logs.** A `logging.directory` set in a repository's `kriya.yaml` is a security setting:
  it needs `kriya authority approve` before it takes effect.
- **Mutating runs** (`generate`, `fix`, milestones, recovery) each get their own run log. Its first line names the run
  ID and the workspace.
- **Read-only commands** (`runs status`, `authority inspect`, `doctor`) write only the application log, or nothing at
  all.
- **`kriya doctor --production`** logs to the terminal only. It checks that the log directory is valid and writable
  (`persistence.logs`) without creating it.
- **Errors:** an invalid or unwritable log directory stops the command with an "Error configuring logging" message.
  Kriya never falls back to `./logs`.
- **Existing `./logs` directories** are left alone, never migrated or deleted.

### 2.0b Where run history goes (`paths.state`)
`traces.db` is the run history behind `kriya traces`. It is persistent state, not log output, so it has its own
location: independent of the log settings, and never derived from the directory you run from.

- **Which directory wins:** the `KRIYA_STATE_DIR` environment variable (absolute), then `paths.state`, then
  `~/.kriya/state`. The database is `<state>/traces.db`.
- **A relative `paths.state`** resolves against the directory of the config file that sets it, never the CWD.
  - Inside a repository it must sit beneath `.kriya/`. `paths.state: .kriya/state` keeps run history in that
    repository.
  - `./state` or `./logs` are rejected with a clear error, so Kriya state never mixes with the repository's own
    files.
  - A value that points outside the repository needs `kriya authority approve`, like the other `paths.*` settings.
- **Read-only:** `kriya traces` never creates the database.
- **`kriya doctor --production`** checks the state directory (`persistence.traces`) separately from the log directory
  (`persistence.logs`).
- **Not affected:** each workspace's own run control state (`.kriya/`: the run lock, run records, checkpoints) stays in
  the workspace, because crash recovery (`kriya runs status|recover`) depends on it.

#### Migrating from `paths.logs` and `logging.file` (removed)
- **Both settings are gone.** Older Kriya versions had `paths.logs` (the log directory and `traces.db`) and
  `logging.file` (the log file path). A config that still names either fails to load with a message naming the
  replacement. Delete the line, then set `logging.directory` and/or `paths.state` if you want non-default locations.
  To turn the application log off, use `logging.file_enabled: false`.
- **Copying old run history.** To keep a `traces.db` from before the move, run
  `kriya traces --migrate-legacy --legacy-path /absolute/path/to/old/traces.db`.
  - Without `--legacy-path`, it uses the old default location, `<Kriya install dir>/logs/traces.db`.
  - The copy is refused if a database already exists at the new location, and the old file is never deleted.
  - `kriya traces` and `kriya doctor --production` both point this out while an old database is present.

### 2.0c Contained verification toolchains (`autonomy.contained_execution_required`)
With contained execution required, compile, test and runtime checks run in a prebuilt, versioned container
image chosen from the project's declared toolchain. Kriya never installs a compiler or JDK during a run, and never
falls back to the host's tools.

| Project | How the version is chosen | Image |
|---|---|---|
| Maven | `pom.xml` `maven.compiler.release`/`source`/`target`/`java.version` (all must agree; `1.8` means 8) | `maven:3.9-eclipse-temurin-<8/11/17/21>` |
| Gradle | `JavaLanguageVersion.of(N)` / `sourceCompatibility` / `targetCompatibility` | `gradle:8-jdk<8/11/17/21>` |
| Python | `pyproject.toml` `requires-python`, else `.python-version` (minor version) | `python:<3.10-3.14>-slim` |

- **Defaults.** With no declared version: JDK 21 for Java and Python 3.12.
- **Baseline and target toolchains.**
  - The repository's declaration is the *baseline*.
  - A toolchain change is a *migration*, and its candidate gates run under the *target*.
- **Authority for a migration** comes only from the run's write scope, never from goal wording. The run must be
  allowed to modify the root `pom.xml`/`build.gradle[.kts]`/`pyproject.toml`/`.python-version`:
  - an unrestricted `kriya generate` is;
  - a scoped or planned run is if the file is in its allowed files or in the approved plan.
- **Outcomes:**
  - **Ordinary task:** the candidate keeps the declaration, so gates run under the repository's version.
  - **Authorized migration:**
    - the candidate changes the declaration (e.g. Java 17 → 21), so gates run under the target;
    - a goal-stated JDK that differs from the declaration (Kriya selects it when the host's `java` and `mvn` disagree)
      is also used as the target, even before the declaration edit lands.
  - **No authority:**
    - a scoped subtask that cannot touch `pom.xml` but whose goal needs Java 21 on a Java 17 repository stops before
      any candidate command runs, with `TOOLCHAIN_REQUIREMENT_CONFLICT`;
    - so does a candidate that changed the declaration without authority.
- **Evidence:** each gate's `toolchain_identity` records `selection`: the baseline, the target, the basis, and whether
  the declaration was mutable. For a candidate checkpoint, the resume fingerprint follows the candidate's own
  declaration. A migration's gates are reused only while the target image is unchanged.
- **Python specs.** `requires-python` supports the usual PEP 440 forms: `>=`, `<`, `!=`, `~=`, `==X.Y.*`,
  major-only bounds such as `<4`, and patch bounds. Kriya keeps 3.12 if the spec allows it, otherwise it picks the
  nearest allowed minor. If only some patch releases of that minor satisfy the spec (e.g. `>=3.11.4,<3.12`), the
  image's exact version is checked when it is attested. `===`, pre-releases and unsatisfiable specs are refused.
- **Attestation.** Before running anything, Kriya:
  - checks the image's immutable content digest;
  - runs the image's own `java -version`/`python3 --version`, plus `mvn --version` or `gradle --version`, to prove
    it holds what was required;
  - runs by that digest, never the tag.

  A mismatch is a `CONTAINMENT_SETUP_FAILED` stop, never a pass.
- **Evidence.**
  - The attested identity is saved with each passing gate in the run trace (`toolchain_identity` in
    `gate_outcomes`): image, digest, and required and observed versions.
  - It is also the toolchain fingerprint that resume and milestone reuse compare (§3.4). A different image digest or
    required version means the gates run again.
- **Unsupported.** Other versions (e.g. Java 7, Python 3.9) and toolchains without a profile fail closed.
  `kriya doctor --production` reports this as `toolchain.required`.

### 2.0d Outbound network authority (egress)
Each outbound network channel runs under one capability class:

| Class | Meaning |
|---|---|
| `denied` | No network |
| `registry_only` | Package registries only, through a scoped proxy |
| `approved_public_knowledge` | Opt-in, read-only public lookups |
| `explicit_destinations` | Exactly the endpoints the configuration names |
| `unrestricted` | The execution environment's network: a privileged setting |

Destinations only ever come from trusted configuration, which SEC-009 protects, or from fixed platform policy.
Repository text and model output can ask for a destination, but they never authorize one.

| Channel | Default | Production profile |
|---|---|---|
| Model and embedding endpoints | `llm.*`, `llm_chain`, `agent_llms`, `embedding.base_url`. Under `egress_policy: local_only`, a non-local endpoint is refused on every call. Embeddings obey this too. | Same (`local_only` is forced) |
| Generated-code verification | Host network (no containment) | `denied` (contained) |
| Dependency acquisition | Host network | `registry_only`: `autonomy.acquisition_registry_hosts` |
| Shell tool (`kriya tools execute shell`, plan tool steps) | `autonomy.shell_network: unrestricted` | `denied`. Recognized package managers stay `registry_only`. |
| KnowledgeGuard release metadata | Public registries (`knowledge.offline_mode: false`) | Off (`offline_mode: true`, cached data only) |
| Live lookup | Off unless `autonomy.web_lookup_enabled` and `search.base_url` are both set | Same: explicit opt-in, sanitized terms, per-query approval |
| MCP servers | Host network | The server's declared `capabilities.network`, enforced in OCI. `explicit_destinations` is not enforceable yet, so the server is refused. |

- **Refusals are fail-closed.** `autonomy.shell_network: denied` without a real containment backend refuses the
  command rather than running it on the host. With containment required, the shell tool is always contained, even
  if `sandbox_execution` is off.
- **Evidence.**
  - Every run records its channels in the trace as the `egress.authority` run event: class, allowed or not,
    destinations, the configuration field they come from, and the containment.
  - Every contained command also records its own `egress` decision in its gate outcome, or in the shell tool's
    result.
- **The doctors never probe a refused endpoint.** Under `local_only`, a non-local model URL is reported as an error
  and is never contacted, and the API key is never sent.

### 2.0e Model runtime identity, qualification and budgets

**Exact runtime (PRD-013).** A tag such as `qwen3-coder:30b` is not an identity: the weights, quantization, chat
renderer, tool-call parser, tokenizer, runtime parameters and server version can change while the tag stays the
same. Kriya fingerprints the exact runtime from what the configured endpoint reports (Ollama `/api/version`,
`/api/tags`, `/api/show`, next to its `/v1` API; never the internet, and never a non-local endpoint under any
egress policy). A component the server does not report is recorded as `unavailable`, never guessed from the name. The
fingerprint also covers the Kriya side of the protocol: the resolved capability profile, the served context window
(`num_ctx`) and Kriya's protocol-adapter version. It is *exact* only when the artifact digest and server version are
known.

Every model call records its fingerprint (`last_call_metrics`, the Developer's `model_use` run event), each run's
RunRecord lists the fingerprint ids it used, the run's `model.runtime` event carries the primary model's full
fingerprint, and each exact fingerprint is stored once under `<state dir>/model_runtimes/<digest>.json`.
```bash
kriya model fingerprint [--model <name>] [--json]
```

**Qualification (PRD-014).** A model name, benchmark or campaign reputation is never a qualification. `kriya model
qualify` runs the protocol cases Kriya relies on against the exact runtime: plain completion, stop finish reason,
structured and multi-line JSON, native, multiple and argument-exact tool calls, streaming assembly, truncation
detection, hidden reasoning, raw full-file content, the anchored edit protocol, malformed-output recovery, timeout,
cancellation, endpoint errors, tokenizer measurement and near-window context capacity (one request filling the
served window to within a few hundred tokens, with markers at its start and end that must both come back). Each case
is PASS, FAIL or UNAVAILABLE with its evidence
(UNAVAILABLE is never PASS; endpoint restart is not exercised live and is always UNAVAILABLE). Model output is never
executed on the host during qualification.
```bash
kriya model qualify [--model <name>] [--json] [--out report.json]   # a --case subset is reported, never saved
kriya model status [--json]                                          # every role's models and their state
```
Records live outside any workspace (`~/.kriya/qualifications/<fingerprint>.json`, or `KRIYA_QUALIFICATION_HOME`), so a
repository can never ship its own. A record is **stale** when the runtime fingerprint, Kriya's protocol adapter or the
qualification policy version changes. Each role requires the cases for the protocols Kriya uses with it: always
completion, stop, truncation detection, reasoning handling and endpoint errors; the Developer also full-file content,
the anchored edit protocol and malformed-output recovery; the Planner malformed-output recovery; and every role the
tool-call, JSON, multi-line JSON and streaming cases its resolved capability profile enables. `kriya doctor
--production` fails `model.qualification` unless every model each role can call (its binding and escalation chain)
has a current record with those cases passing.

**Normalized completion results (PRD-015).** Provider quirks are handled at the LLM boundary. Every call produces a
normalized result with a status: `OK`, `EMPTY_CONTENT`, `MALFORMED_STRUCTURED_OUTPUT`, `OUTPUT_TRUNCATED`,
`BACKEND_ERROR`, `TIMEOUT` or `CANCELLED`, plus the finish reason, token usage, reasoning presence (never its text),
normalized tool calls (native, or Hermes/Qwen-XML text the server's parser did not convert) and the runtime
fingerprint. A Developer answer the provider cut off at its output budget is never written as a file, even when the
partial text would compile; the attempt fails with `OUTPUT_TRUNCATED` and retries.

**Prompt allocation and dispatch budgets (PRD-016, CTX-001).** Context assembly plans with a `len/4` estimate, but
it sizes every section against the call's *prompt allocation window*: the served window minus the output budget
(`max_tokens`, at most half the window), the request framing and a 256-token safety margin, converted with the same
counting ratio the final check uses. Within it the graph pool (skills, learned knowledge, retrieved code, known-target
and member source; the design and plan are reserved first) gets 0.60, retry evidence 0.15, already-written sibling
files 0.15, and opt-in investigation evidence at most 0.10. When even the signature-level form of the retrieved files
does not fit, the lowest-ranked files are left out, recorded as `budget_exhausted` and named in the prompt; nothing is
cut silently. A fully allocated Developer prompt at 32K/16384 therefore keeps its whole output budget. Retrieved
context is smaller than before this change: the old fractions of the raw window produced prompts larger than the
window itself, which the server truncated.

The final check before a request leaves Kriya counts the whole dispatch (system and user prompts, every message, tool
schemas and framing). Counting uses an exact tokenizer when one is registered for the runtime's tokenizer (none is by
default: Ollama 0.34 has no tokenize endpoint and Kriya downloads nothing), else the qualified ratios measured for that
tokenizer, else a documented approximation (2.5 ASCII bytes and 1.5 non-ASCII bytes per token). Every call records
which method was used and compares its prediction with the provider's reported usage. The preferred window is the
served `num_ctx` when known, otherwise `llm.context_window` as a declared assumption; set
`llm.extra_body.options.num_ctx` so the two agree.

**Adaptive budget.** The configured window and `max_tokens` are *preferred* values. With
`llm.context_policy.mode: adaptive` (the default) a request that does not fit its preferred window is sent with the
**smallest larger context tier that is qualified** for the exact runtime, and nothing larger:
```bash
kriya model qualify --context-window 65536   # qualifies the same model at num_ctx 65536 (its own fingerprint)
```
A tier counts only with a current record at that `num_ctx` that passes `context_capacity` and every case the model's
roles need. While no qualification data exists for a size, `llm.context_policy.declared_safe_context_tiers` may name it;
a failed or stale record overrides that declaration. A tier is never above `llm.context_policy.max_context_tokens` or
the model's trained length, is never chosen because the machine has spare memory, and can only be selected on an exact
Ollama runtime (elsewhere adaptive behaves like strict and records why). The output budget grows above `max_tokens`
only for a *grounded* expectation (the Developer's full-file rewrite of an existing file is expected to be about that
file's size), never because a model produced more than expected, and never above
`llm.context_policy.max_output_tokens`. `strict` never exceeds the preferred values. Every automatic enlargement
(context or output) is logged and recorded as a `model.budget_expansion` run event in the trace, with the preferred and
selected window and output, the prompt and expected output tokens, the reason, the tier's qualification source and the
ceilings and the call's elapsed time. Ollama sizes a model's context when it loads it, so a request with a different
`num_ctx` can make it reload the model (check with `ollama ps` on your host): an expansion can cost a reload and more
memory, and calls alternating between windows can repeat it. Qualify a tier only if the host can hold it.

When nothing allowed fits, a Developer file request is first sent once more without the contents of the files already
written in the same batch (names only; `model.optional_context_reduced`). If it still cannot fit it is refused before
inference: `CONTEXT_BUDGET_UNSATISFIABLE` when the prompt itself cannot fit, `OUTPUT_BUDGET_UNSATISFIABLE` when the prompt fits but the grounded output cannot. For a full-file
rewrite of an existing file the Developer then asks once for an anchored patch instead (models whose capability profile
accepts patches), recorded as `model.output_budget_protocol_fallback`; otherwise the attempt fails with the typed
reason. An answer the provider cuts off is `OUTPUT_TRUNCATED` and is never written; a retry does not enlarge the output
unless the expectation is grounded. `llm.context_policy` is SECURITY_AUTHORITY: a repository cannot grant itself a
larger window.

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

### 2.1a Fallback transitions, role independence and model routing (`model_policy`)

**Fallback transitions (PRD-017).** When the Developer's retry moves to an `llm_chain` model, everything the request
depends on is resolved again for that model: its exact runtime and Developer qualification, its capability profile
(native tools, JSON mode, edit protocol, streaming), reasoning, its served and prompt-allocation windows and its own
output budget. A chain entry's `max_tokens` is now used for every call to it. Left unset, it is the shared default
output budget (16384, the packaged `llm.max_tokens`), never your primary model's own `llm.max_tokens`; the per-call
budget still follows the model's window (§2.0d). Each change of model is recorded field by field as a
`model.transition` run event. A fallback cannot serve an attempt when:
- a qualification record for its exact runtime has a failed Developer case;
- `runtime_profile: production` is set and the runtime is not QUALIFIED;
- the attempt may only patch an existing file (no complete current source was shown) and the model's profile returns
  whole files only;
- its window leaves no room for a prompt.

Such a fallback is skipped, and is sent no request: the attempt goes to the next configured fallback that can serve
it, in your `llm_chain` order. Each skip is recorded as a `model.fallback_selection` run event (the requested model,
every rejected one with its reasons, the selected one). Only when no remaining fallback can serve does the run stop
with `FALLBACK_MODEL_INCOMPATIBLE` (`failure_category: fallback_model_incompatible`), listing every rejection. A
runtime that has never been qualified is recorded, not refused. Compatible fallbacks are never reordered, and a
larger model is never assumed to be more capable.

**Role independence and per-role metrics (PRD-018).** By default every role may share the Developer's model, which
is the fast local setup. Roles that share one runtime also share its errors, so `kriya doctor --production` reports
which roles share an exact runtime (`models.role_independence`, WARN) by fingerprint, not by model name. To require
independence for selected roles:
```yaml
model_policy:
  independent_roles: [run_verifier, spec_compliance]   # each must run on a runtime distinct from the Developer's
agent_llms:
  run_verifier:
    llm: {model: "qwen3.5:9B", base_url: "http://localhost:11434/v1"}
  spec_compliance:
    llm: {model: "qwen3.5:9B", base_url: "http://localhost:11434/v1"}
```
`generate`, `fix` and proposal execution refuse to start (`ROLE_INDEPENDENCE_REQUIRED`) when a listed role shares the
Developer's exact runtime, or when either runtime cannot be identified exactly. The doctor row then becomes blocking.
A second model's opinion is still never verification evidence: only the deterministic gates decide whether an
attempt passed.

Every call is counted per role and exact runtime:
- calls and latency;
- tokens;
- protocol failures (empty, malformed, truncated, backend error, timeout);
- schema failures (a response the role could not use, e.g. JSON that fails its contract);
- for the Developer, its attempts, first-pass successes and the retries its failed attempts triggered.

Each run stores its rows as a `model.role_metrics` event. `kriya model metrics [--json]` aggregates them over the
runs in `traces.db`.

**Evidence-based routing (PRD-019, opt-in).** Routing picks, per role, one runtime from candidates you configure:
```yaml
model_policy:
  routing:
    mode: evidence            # off (default) | evidence | frozen
    candidates:
      - {model: "qwen3-coder:30b", base_url: "http://localhost:11434/v1"}
      - {model: "qwen3.5:9B", base_url: "http://localhost:11434/v1"}
    roles:
      reviewer: ["qwen3-coder:30b", "qwen3.5:9B"]     # operator preference order
    min_calls: 5              # fewer measured calls than this = unmeasured
    # table_path: /abs/path/model_routing_table.json  (default: the state directory)
    # frozen_routes_path: /abs/path/frozen_routes.json (required for mode: frozen)
```
A candidate is eligible only when its runtime is exactly identified and QUALIFIED for that role *as routed*: the one
qualification record of that exact runtime must pass every case the role requires (the base cases, the role's own,
and each protocol its profile enables). One record serves every role its cases cover; a runtime that passes the
Planner's and Reviewer's cases but failed a Developer case such as `anchored_edit_protocol` is still not routed to
the Developer. A model's runtime identity depends on the binding it is placed in (the Developer's is the primary
`llm`, every other role's an `agent_llms` binding), so qualify a candidate for its route with
`kriya model qualify --model <candidate> --role <role>`. A JSON role needs a JSON-mode profile, and a candidate's
window may not be smaller than the role's configured one (or `min_context_window`). An explicit
`agent_llms.<role>.llm` wins whenever it is qualified.

Among eligible candidates, routing ranks by Kriya's own measured outcomes for that role on that exact runtime: the
Developer by the share of attempts that passed the gates, other roles by the share of calls with no protocol or
schema failure. Measured candidates come first; ties go to operator order, then alias. Model names, sizes and
model judgments are never inputs. With no eligible candidate the role keeps its configured binding.

The metrics come from a table you refresh between runs with `kriya model metrics --write-table`; a run only reads
it, never updates it. Every decision is recorded as a `model.route` run event: the candidates, each rejection
reason and the final route. `kriya model routes` shows the decisions without running anything.
`kriya model routes --freeze <path>` records them, and `mode: frozen` then replays exactly those runtimes for
reproducible certification, refusing the run (`ROUTE_FROZEN_MISMATCH`) if any runtime changed or is no longer
qualified. Routes are sticky within a run: each checkpoint records them, and `--resume`/`--resume-id` (direct or `--from-milestones`) reuses
exactly the resumed run's own routes instead of routing again on a table that changed since, or refuses the resume
(`ROUTE_RESUME_MISMATCH`) if a recorded runtime changed or is no longer qualified. `model_policy` is SECURITY_AUTHORITY: a repository cannot set or relax it.

### 2.1b Your original requirements (PRD-020)

Kriya fixes your goal's own statements before any model reads them. Each list item, and otherwise each sentence,
becomes a requirement `REQ-1`, `REQ-2`, ... with your text verbatim. The split is deterministic, and no model
rewords it. The Planner and the Architect are shown the list and asked to cite the ids they serve; the enforce
Planner maps subtasks to them (`requirement_ids`). A plan that paraphrases or leaves one out cannot remove it.

Only the verifier (the Goal Spec Compliance check, after the compile/test gates passed) records an outcome, bound
to the exact files it judged:
- `satisfied`;
- `violated`: a concrete, literally-named requirement is missing, so the attempt fails and the retry is told
  `REQ-n: <your text>`;
- `unverified`: behaviour that cannot be confirmed from source;
- `unknown`: no verdict.

A violated requirement always blocks success. `autonomy.requirement_unknown_policy` and
`autonomy.requirement_unverified_policy` (`record`, the default, or `block`) decide the other two. `block` stops
before anything is applied, with `[REQUIREMENTS UNRESOLVED]`. The production profile seals the unknown policy to
`block`. Both fields are SECURITY_AUTHORITY.

Which unit verifies the requirements:
- a direct run;
- a milestone plan's final integration unit, against the plan's original goal (a milestone verifies only its own goal);
- enforce mode's terminal `original_requirements` gate.

The result's `requirements` field, and the `requirement.derived`, `requirement.lineage` and `requirement.verdicts`
run events, record everything. A resumed run derives the same ids from the goal again.

### 2.2 Control Plane, Policy, and Structured Execution

A second, opt-in configuration layer sits alongside the pipeline above - classifying how much process a request deserves, enforcing what it's allowed to touch, and (optionally) executing it as a validated set of bounded subtasks instead of one long undifferentiated run. See `docs/design.md` §8 for the full architecture and rationale; this section is the config reference. Every field below defaults to leaving current behavior completely unchanged.

**The fast path - `runtime_profile`**: instead of hand-tuning the individual fields below, pick one named preset:
```yaml
runtime_profile: hardened   # or: legacy | validated | (omit for no change)
```
- `legacy` - the original pipeline only; equivalent to leaving this whole section unset.
- `validated` - triage and process profiles are live, structured plans run in shadow mode (real LLM/control-plane work happens, but never affects the real generated output).
- `hardened` - `validated` plus real enforce-mode execution: bounded, per-subtask write authority and fail-closed verification.

A `kriya.yaml` may set `runtime_profile` **or** hand-tune `engineering_triage`/`process_profiles`/`workflow_controller` directly, never both - combining them raises a config error at load time. `runtime_profile` never touches `execution_policy.mode` (see below) - that stays audit-only regardless of preset.

**`engineering_triage`** - classifies each request's shape (`task`/`enhancement`/`milestone`/`refactor`) and risk:
```yaml
engineering_triage:
  enabled: false      # turn classification on and log it
  shadow_mode: true   # keep the result from affecting anything (must stay true until process_profiles.enabled is also true)
```

**`process_profiles`** - whether a resolved classification actually changes pipeline behavior (context depth, approval requirements):
```yaml
process_profiles:
  enabled: false
  enforce_approval: false
  enforce_context_depth: false
  enforce_verification_depth: false   # rejected if set true - verification depth is telemetry-only by design; a triage misclassification must never be able to reduce regression-test coverage
```

**`execution_policy`** - audit logging of consequential actions (file writes, commands, network calls, package installs, git operations) against a deterministic policy engine:
```yaml
execution_policy:
  enabled: true    # default on - this has been logging safely at 6 real call sites since it shipped
  mode: audit      # "enforce" is rejected at config load time; audit-only by binding decision, independent of runtime_profile
```
This is audit/telemetry only - it never denies or blocks an action on its own. The one real enforcement Kriya applies today is a narrow, separate, always-on mechanism (goal-source-file protection, workspace containment, a small set of hard-invariant denials) that isn't gated by this section at all - see `docs/design.md` §8.5.

**`workflow_controller`** - routes real `generate`/`generate --from-milestones` calls through structured, validated per-subtask execution:
```yaml
workflow_controller:
  enabled: false   # off by default - shadow mode still makes real LLM calls of its own, budget/network accordingly if you turn this on
  mode: shadow     # "shadow": runs alongside the real pipeline, never affects its outcome. "enforce": real bounded per-subtask execution and write authority.
```
`mode: enforce` requires `enabled: true`. Under enforce, every `MODEL`-tagged subtask must declare a non-empty set of files it's allowed to write (`planned_files`) or the plan is rejected outright rather than falling back to an unbounded whole-goal write; every write is checked against that exact allowlist. `TOOL`-tagged subtasks (arbitrary tool/command execution) are refused outright in enforce mode, not silently skipped.

**Operator visibility**: `kriya doctor` reports the current workspace's control-plane state regardless of whether any of the above is enabled - workspace identity, whether `ControlState` matches the real on-disk workspace (drift detection), and contract/artifact record counts.

**Task correctness across revisions (MA8)** - no new config, active automatically whenever `mode: enforce` runs. On top of the bounded per-subtask write authority above, `enforce` mode now tracks whether structural plan constraints and (for a dependency-migration goal) the migration's own completion facts stay true across plan repairs and retries - a repair that fixes one constraint while silently regressing another already-fixed one is now detected and fed back into the next repair attempt, a stale or contradictory Goal Spec Compliance verdict can no longer override an already-confirmed deterministic migration fact, and a recurring invented parallel implementation of an existing file's responsibility is now flagged rather than repeatedly re-patched. See `docs/design.md` §9 for the full mechanism.

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
`generate` (and `fix`, below) checkpoint after each stage - Plan, Design, and Developer output that's already passed Quality Gates - to `.kriya/checkpoints/` in your workspace. If a run gets killed or crashes partway through, re-run the *exact same command* (same goal, same workspace, same config) with `--resume` to pick up the most recent checkpoint of a plain `generate`/`fix` run (a milestone's checkpoint is never picked up here - see §3.4.1), or `--resume-id <id>` for a specific one (the `id` is printed if the run finishes without quality gates passing):
```bash
kriya -c kriya.yaml generate "Create a Spring-XML Java 17 app running Ignite 2.18.0" --resume
```
This is opt-in only - Kriya never guesses that you're resuming from goal text alone. A checkpoint is deleted the moment its run finishes normally (success or an explicit rejection at the approval gate) - it only ever survives a kill/crash, or a run whose Quality Gates never passed. Requires the workspace to be a git repository.

**What a resume reuses (PRD-008).** Kriya compares what the checkpoint was built from with what is true now, and keeps only the work nothing has invalidated. The run log shows `Resuming checkpoint '<id>' partially: reusing ...` or `Refusing to resume checkpoint ...` with the reasons, and the decision is recorded as `resume_decision` on the run record (`.kriya/control/runs/`).

| What changed since the checkpoint | What happens |
|---|---|
| The workspace content, the config, the goal/error text, the skills, or the Kriya installation itself | Nothing is reused: a fresh run |
| The predetermined plan, the incoming obligations, or the write-scope/semantic-region authority | Planning is redone |
| The candidate's saved bytes don't match their recorded digest, or the obligation ledger it was built against is missing or different | The plan and design are kept; the candidate is regenerated |
| The containment, toolchain or verification settings | The candidate is kept, written into a fresh worktree through the normal write checks, and its gates run again |
| Only the model identity (provider, model, base URL or API key, for the primary model or a per-role `agent_llms` model) | Nothing is dropped. Any other `llm`/`llm_chain`/`agent_llms` setting is part of the config: a fresh run |

A value Kriya cannot determine counts as changed. The toolchain identity is known only under contained execution (§2.0c): the required toolchain plus the local image's content digest. On the host it is unknown, so a resumed candidate always has its gates run again. Terminal full regression always runs before anything is applied, and a checkpoint whose run record has disappeared reuses nothing.

**A crashed commit blocks every mutating command until it is settled.** If an earlier run died while writing files into the workspace (or its run record or commit evidence is unreadable), `generate`, `fix`, `generate --from-milestones`, `proposal execute`, and `tools execute` for a call that may write to the workspace, refuse to start, before any model call, and exit 1 with:
```
[Recovery Required] The workspace may hold a partial commit from an earlier run (runs with unsettled/uncertain commits: <run id>). Run `kriya runs recover` to settle it from the durable evidence.
```
See [Inspecting and recovering runs](#342-inspecting-and-recovering-runs-kriya-runs) below.

#### 3.4.1 Milestone-Based Goal Decomposition (`plan-milestones` + `generate --from-milestones`)
A single `generate` call has a fixed token budget the whole response has to fit inside - for a goal combining several real subsystems (e.g. a cache plus a message broker plus the wiring between them), that budget forces reasoning and content to compete, and the highest-risk code (the integration/wiring logic, usually written last) is the first thing cut off when the budget runs out. Milestone decomposition splits a goal like that into an ordered sequence of small, independently executable and verifiable slices before any code is generated.

**Step 1 - propose a plan** (writes a file, executes nothing):
```bash
kriya -c kriya.yaml plan-milestones "Build a Java app combining an embedded Ignite cache and a Qpid AMQP broker, wired together"
```
The Milestone Planner slices by *observable runtime behavior*, never by code structure - a milestone is never "write these classes" or "implement the X layer," it's "the smallest next slice of real, runnable, checkable behavior." For the example goal above, this produces something like:
```
1. Start the cache node, confirm it started, shut it down cleanly - no cache definitions, no messaging yet.
2. Define one cache, write one object to it, read it back, print it - still no messaging.
3. Start the broker alongside the cache, send one message, consume it back synchronously.
4. Wire the two together - the consumed message's payload is what gets stored in and read back from the cache.
```
Each milestone carries its own plain-language success criterion, which becomes that milestone's own runtime-verification target. The plan is written to `.kriya/milestones/<group_id>.plan.json` - review it, and hand-edit the file directly if a slice looks wrong (merge two that don't stand alone, reorder them, tighten a criterion) before running anything.

**Step 2 - execute the (possibly edited) plan:**
```bash
kriya -c kriya.yaml generate --from-milestones .kriya/milestones/<group_id>.plan.json -y
```
This calls the exact same, unmodified Developer + Quality Gates loop as an ordinary `generate` call, once per milestone, against the same growing workspace - so milestone 2 sees milestone 1's real, already-applied output on disk (a plain file copy, never a git commit, so there's nothing new to keep in sync). Two checks run here that a single-goal `generate` doesn't need:
- **Dependency-drop guard**: after each milestone, Kriya diffs the current `pom.xml` against every dependency established by prior milestones. If one silently disappeared (a real, previously-observed failure mode across a long multi-attempt run), the milestone is treated as failed rather than letting the regression through.
- **Prior-milestone verification replay**: before the final integration pass, Kriya re-executes every already-completed milestone's own captured runtime-verification command against the *current* workspace and re-checks its `[VERIFICATION]` marker - catching a later milestone that quietly broke an earlier one's behavior. Nothing else in Kriya covers this: the end-of-run regression suite is framework-tests-only, and a normal run never re-verifies an earlier goal's behavior while working on a later one.

A final **integration pass** then runs once more against the whole assembled project - goal text synthesized from the original goal plus every milestone's own success criterion, explicitly told not to rewrite working code, only to confirm the complete system and fix what's actually broken.

**If a milestone fails** (exhausts its own retry budget, or trips the dependency-drop guard), you're asked to abandon (stop here, keep whatever earlier milestones already applied) or retry it from scratch - never a silent skip-ahead, since a later milestone building on a known-broken one directly contradicts the whole point of slicing by runnable behavior. Under `-y`, this defaults to abandon.

**Resuming**: re-running the identical `generate --from-milestones <file>` command picks up from the last completed milestone via a sidecar state file (`.kriya/milestones/<group_id>.json`) instead of restarting the whole sequence - but the milestones/goal themselves are always re-read fresh from the plan file, so an edit you make to the plan between runs still takes effect.

**Which milestones a rerun skips (PRD-008).** Being listed as completed in the sidecar is not enough on its own. A completed milestone is skipped only when durable evidence proves it:
- **A milestone that changed files** is skipped only when all of these hold:
  - its commits are COMMITTED in the run record and in the commit evidence under `.kriya/control/`;
  - every file it last wrote still has exactly the committed bytes and file mode;
  - its definition in the plan file is unchanged.
- **A milestone that changed nothing** is skipped only when all of these hold:
  - every one of its structured acceptance criteria is covered by deterministic evidence that ran and passed in its final attempt: a specific test, compile check or runtime verification mapped to that criterion. A model saying "no change needed" never counts, nor does an unrelated passing test, nor a test command that ran zero tests;
  - the verification settings are unchanged;
  - the toolchain that produced that evidence is known and unchanged;
  - the workspace is exactly what the milestones completed since then explain. Untracked files under generated-output directories the repository model already ignores (`__pycache__`, `target`, `build`, `dist`, `node_modules`, `.venv`, ...) do not count; any other new or changed file, tracked or not, does.

  In practice this path is not reachable yet. Milestone acceptance criteria are free text, and nothing maps them to specific checks. The toolchain identity is also only known under contained execution (§2.0c). So a milestone that changed nothing reruns. A milestone that rewrites its file with identical bytes is an ordinary change and is skipped as described above.

Anything else reruns that milestone and everything that depends on it. An unrelated milestone that wrote the same files also reruns. Other milestones stay skipped. A file that several milestones and the integration pass wrote is checked against its last committed state. If it differs, the last completed milestone that wrote it reruns, with the reason `CURRENT_BYTES_DIVERGE_FROM_COMMITTED_LINEAGE`.

**If a run crashed between a milestone's commit and saving its progress,** the rerun rebuilds that milestone's completion from the run record and commit evidence instead of redoing it, provided the evidence proves the exact committed output. If it doesn't, the milestone reruns. This also applies when the crash was inside the commit and `kriya runs recover --complete-partial` finished it. The recovery record must show that recovery completed exactly the interrupted, authorized commit: every file was applied or rolled forward, and none was foreign or ambiguous. The rebuilt completion is marked `completion_origin: RECOVERY`. The recovered run keeps its own status (RECOVERED, with NEEDS_REVIEW or FAILURE); it never becomes a success. The reason for every skip, rerun and rebuild is recorded under `milestone_reuse` in the command's JSON result, in the run record and in the sidecar.

**A rerun never restores earlier output.** A milestone that reruns because you edited, deleted or reset its files works on the files as they are now. Your edits are treated as intentional and are never rolled back. Kriya only decides whether earlier work may be reused; it is not an undo system.

**`--resume` with milestones** offers each milestone only its own newest checkpoint: same sequence, same milestone, same definition. The normal resume checks still decide whether that checkpoint is reused. A checkpoint that belongs to another milestone, or that was saved before this rule existed, is never offered, and the milestone starts fresh. An explicit `--resume-id` is offered only to the milestone that saved it.

**One execution model (PRD-008A).** A plain `generate` goal and a milestone sequence run through the same executor: a plain goal is a plan with one work unit, a milestone sequence is a plan with one work unit per milestone plus the integration unit. The run record (`.kriya/control/runs/`) holds the plan as `execution_plan` and each unit's outcome as `work_unit_states` (`VERIFIED`, `FAILED`, `BLOCKED`, `STALE`, with reasons). When a milestone fails, every milestone after it is `BLOCKED`: `DEPENDENCY_FAILED` if it depends on the failed one, `PLAN_HALTED_AFTER_FAILURE` otherwise (the sequence still stops at the first failure). A milestone sequence's JSON result carries the same `work_unit_states`. A run is never recorded as a success while any unit is not `VERIFIED`. A hand-edited plan file with a dependency cycle or a dependency on a milestone that does not exist is refused up front (`milestone_plan_invalid`) instead of silently dropping those milestones.

Deliberately opt-in - there's no automatic "this goal looks too big" detection. The Milestone Planner's own slicing quality has to earn your trust goal by goal; auto-triggering on every large-looking goal would silently change behavior for every existing user with no proven size heuristic behind it.

#### 3.4.2 Inspecting and Recovering Runs (`kriya runs`)
Every mutating run writes a run record under `.kriya/control/runs/`, and every commit into the workspace writes commit evidence under `.kriya/control/commits/` first: the exact bytes and file mode each file had before and must have after, plus the digest of the approved candidate. `kriya runs` reads that evidence. It works even when the current configuration is denied by authority checks, so a bad `kriya.yaml` can never block recovery.

```bash
kriya runs status                      # read-only: what crashed, and what recover would do
kriya runs recover                     # settle what the evidence proves
kriya runs recover --complete-partial  # also finish a half-applied, commit-eligible candidate
kriya runs prune --dry-run             # reference-safe cleanup of old records and evidence
```
Each command takes `--workspace <dir>` (default `.`) and `--json`. Exit codes: **0** nothing needs attention, **1** action required (`status` returns 1 for anything other than CLEAN or RUN_ACTIVE, including "recovery available"), **3** a live Kriya run holds the workspace.

- **`status`** never takes the workspace lock and never writes anything (it does not even create `.kriya/`). While a live run holds the workspace it reports `RUN_ACTIVE` and classifies nothing. Otherwise it compares every file of every interrupted commit with its recorded before/after state: `APPLIED`, `NOT_APPLIED`, `FOREIGN` (neither - someone else changed it) or `AMBIGUOUS` (old text-only evidence that can't tell the states apart).
- **`recover`** takes the workspace lock. A commit whose files are all applied is settled COMMITTED; one with none applied is ROLLED_BACK; a run with no commit evidence at all never touched the workspace (NOT_COMMITTED). The run record becomes **RECOVERED**, with `terminal_status` NEEDS_REVIEW if anything was or may have been committed (the run's post-commit steps never ran) and FAILURE otherwise. A recovered run is never reported as a success. Plain `recover` never changes a source file.
- **`--complete-partial`** finishes a commit that stopped halfway, from the staged files it left beside their targets. It is allowed only when the run record proves that exact candidate had passed its gates and reached the commit (the record's candidate digest, the evidence's digest and the digest recomputed from the bytes on disk all agree), and every missing write has exactly one intact staged file. There is **no rollback** after a crash: Kriya does not keep the original bytes durably, so a partial commit is either finished or left for you.
- **Manual action** is reported for anything that can't be proven: a `FOREIGN` or `AMBIGUOUS` file, or an unreadable record or evidence file. Kriya never edits or deletes those. Restore each listed file to its `before` or `after` state (or move an unreadable record out of `.kriya/control/runs/` once you've confirmed it isn't the only evidence of a commit), then run `kriya runs status` again.
- **`prune`** keeps the newest 50 terminal run records by default (`--keep N`) and never removes a non-terminal record, one whose commit state is unknown, one a checkpoint or the control state still references, or evidence a kept record needs. It removes nothing at all while any record is unreadable. The same pruning also runs automatically at the end of every run.

A milestone whose commit was finished by `--complete-partial` is not redone by a later `generate --from-milestones` rerun - see "If a run crashed between a milestone's commit and saving its progress" in 3.4.1 above.

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

#### 3.6.1 Natural-Language Routing (on by default)

Instead of typing an explicit command, you can type what you want in plain English and Kriya will figure out which command to run - on by default since 2026-08-02 (was off; see below for why). Explicit commands are completely unaffected either way: routing only ever activates when the typed line's first word doesn't already match a real command name, so `generate "..."`, `ask "..."`, etc. dispatch exactly as before regardless of this setting. Turn it off in your config if you'd rather every line be an exact command:
```yaml
routing:
  enabled: false
```
Routing needs its own embedding model pulled (separate from whatever `embedding.model` you use for code search - short natural-language phrases and long-form code retrieval are different jobs, and the packaged default embedding model scored meaningfully worse at this specific task in testing):
```bash
ollama pull embeddinggemma
```
If it isn't pulled, routing fails loudly with the exact `ollama pull ...` command needed and disables itself for the rest of the session (falls back to requiring exact commands) rather than silently misbehaving - it does not affect the embedding model used for the code/RAG index either way.
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

**One sharp edge, regardless of whether routing is on**: since an explicit command always wins, a line that happens to *start* with a real command word - e.g. typing `generate a REST API for todos` without quoting the whole request - dispatches directly as `generate` with `"a"` as the goal and everything after it rejected as unexpected extra arguments, rather than being routed at all. Kriya shows a short tip in this situation:
```
╭─ kriya
╰─> generate a REST API for todos
Usage: kriya generate [OPTIONS] [GOAL]
Error: Got unexpected extra arguments (REST API for todos)
(Tip: Kriya commands need exact syntax - wrap a full request in quotes, e.g. generate "..." -y, so it's passed as one argument.)
```
Wrapping the whole request in quotes (`generate "a REST API for todos"`) works either way - explicitly, or picked up by routing if the first word isn't a command name at all.

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
  web_lookup_enabled: true       # both switches required - flipping only one does nothing
  web_lookup_auto_approve: false # true = skip the pre-send confirmation below, allow fully unattended search

search:
  base_url: "http://localhost:8080"  # a search endpoint, e.g. a self-hosted SearXNG instance
  top_k: 3                           # candidate results tried per term before giving up
```
A self-hosted SearXNG instance keeps the *aggregator* local, but by default it still federates queries out to real public search engines (Google, Bing, DuckDuckGo, etc.) - configure it with only offline/local sources if you need outbound network activity bounded further than what's described below.

**What can never leave your machine, even when this is on**: search queries are built *exclusively* from bare technology-name strings a bounded, deterministic code path already extracted from the goal or the Architect's proposed design (the same extraction used for missing-skill detection) - never your actual goal text, design text, or code. This is a hard, code-enforced boundary, not something a model decides at runtime, specifically so a project's proprietary content can never end up in an outbound search request.

**A separate, additional gate on *when* a query is allowed to fire at all**: the content restriction above doesn't by itself mean a query is authorized to leave the machine right now. With `web_lookup_auto_approve` left at its default `false`, every outbound search - across all three trigger points below, including the retry-loop one - shows you the exact terms and target URL and asks for confirmation *before* sending, not just before using whatever comes back:

Even when `web_lookup_auto_approve: true`, unattended lookup is limited to Kriya's built-in public technology catalog plus identifiers explicitly listed in `search.public_terms`. An unknown identifier falls back to the per-query approval callback; without one it is not sent. This prevents a private dependency or internal product name from becoming an unattended query merely because it resembles a library coordinate.
```
[Live Lookup] Kriya wants to search for reference material on:
  - org.apache.ignite:ignite-core
via: http://localhost:8080

Send this search? (only these bare technology-name terms are sent - never goal/design/code/error text)
```
Under `-y` with `web_lookup_auto_approve` still `false`, the query simply never fires at all - a deliberate change from earlier behavior, where a non-interactive run would send the query anyway and then silently discard whatever came back, with no human ever seeing that an outbound request had happened. Set `web_lookup_auto_approve: true` only once you've accepted unattended outbound search (of bare technology names only) as part of your threat model - it skips this prompt entirely, in both interactive and non-interactive runs.

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

**The query itself matters as much as trying multiple results, confirmed live**: Kriya searches for `"{term} example"`, not `"{term} documentation"`. A real side-by-side test against a self-hosted SearXNG instance (and, separately, against a different search backend, using the exact term shape Kriya actually sends) showed "documentation" consistently surfacing a project's landing/index page with nothing concrete, while "example" surfaced a GitHub example config file, an official quick-start code sample, and a wiki how-to page with a real dependency block for the same terms - independently confirmed to extract cleanly through Kriya's own fetch logic. Deliberately kept as a single query per term rather than trying several phrasings - the same live testing also hit real rate-limiting/CAPTCHA suspension on a self-hosted SearXNG instance's own upstream engines after a modest number of requests, so multiplying query volume per term is a reliability risk worth avoiding, not just added latency.

**A third trigger, inside the Developer retry loop**: with the same `autonomy.web_lookup_enabled`/`search.base_url` switches on, a compile or Runtime Verification failure that repeats *identically* on a second consecutive retry attempt also triggers a lookup - a repeated failure suggests the model isn't self-correcting on its own. Nothing is written to a skill's `rules.txt` here (it's folded directly into that one retry's prompt, not durable knowledge) - but it is still subject to the same pre-send confirmation described above, same as the other two trigger points; it is not exempt just because the retry loop itself is otherwise unattended. It only searches for well-known tool/plugin/library coordinates found in the error text itself, never the error or stack trace as a whole - and never the project's own coordinate (read fresh from the worktree's current `pom.xml` on every retry), since Maven's own build banner prints that on every single build and it isn't something a search could ever help with. It also recognizes a wrong-import-path compile failure (e.g. a class imported from the wrong package within a library already declared as a real dependency) - the class name only ever becomes a search term when it's traced back to one of the project's own declared `pom.xml` dependencies, same trust boundary as everything else here. Real testing found this genuinely helps for actual unfamiliar-library knowledge gaps, but many repeated retry failures turn out to be the model losing track of something across a large multi-file response rather than missing information - this feature doesn't fix that class of failure, only the knowledge-gap class.

### 4.7 Targeted Single-File Retry
No configuration needed - this is always on. When a Quality Gates failure (compile or Runtime Verification) names one of the files Kriya already wrote, the next retry focuses on fixing just that file instead of regenerating your entire project again - the target file is shown to the model as the thing to fix, every other file is included as reference material so nothing else gets accidentally rewritten (though the model can still touch another file if the fix genuinely needs it, e.g. a missing import that also needs a new dependency added). This runs on its own budget (3 attempts) separate from the normal retry count, and always uses your primary model - never the fallback chain, since a model swap costs real time and the whole point of a targeted retry is to be fast.

Any retry (targeted or full-set) that's responding to a real prior failure also requires the model to write a short "FIX ANALYSIS" explaining the specific cause before it writes any code, stripped out before the result is saved as file content. Found live: without this, a non-reasoning local model can regenerate byte-for-byte identical broken code across every single retry attempt even with the exact compile error present in every prompt - nothing was forcing it to actually engage with that error before writing code. This is a prompting technique, not a model-capability switch - it has nothing to do with `llm.reasoning` (see §3, which only accommodates a model that already emits its own `<think>` output).

In a full-set retry (regenerating every file in the batch, not just one), this "FIX ANALYSIS" instruction and a snippet of the exact broken source line only apply to the file(s) the compile error actually names - not every file being regenerated. Found live: without this scoping, an unrelated file in the same batch got asked to "explain the fix" for an error it had nothing to do with, producing a wrong, confused analysis instead of the correct one being concentrated where it mattered. The broken-line snippet itself is extracted generically from the compiler's own `File.java:[line,col]` locator, which every compile error carries regardless of its message - not tied to any specific error type.

If a failure doesn't clearly point at one of your files (a bare exit code, a build-tool configuration error with no source file involved), Kriya falls back to a normal full-file-set retry - targeting is a bonus when it can confidently narrow the fix, never a guess.

When a retry knows the exact broken source line, it now prefers asking for a small, anchored patch (a targeted before/after code block) over regenerating the whole file - found live that a full regeneration can correctly state the right fix in its own analysis and then still lose it while rewriting everything else around it. Falls back to full-file content automatically if the fix genuinely needs broader changes.

When `jdtls` is installed (`brew install jdtls`, no config needed) and a retry is fixing a Java file, Kriya also checks it against the project's real, resolved classpath and folds any confirmed error into the same retry prompt - deterministic ground truth for import/symbol mistakes, distinct from (and complementary to) the compile error itself. Optional and automatic: not found, or fails to start, and nothing changes. `kriya doctor` reports whether it was detected.

Java compile checks always pass `-Xlint:rawtypes,unchecked` to javac now, so a raw-type mistake (e.g. using a cache/collection without generics, a real and recurring cause of "incompatible types" failures) shows up as a precisely-located warning right next to the resulting error, rather than javac's default one-line notice with no file/line at all - one more concrete thing the fix-analysis step above has to work with.

### 4.8 Completeness Prevention & Missing-File Recovery
No configuration needed - this is always on. Before the Developer generates anything, Kriya scans the Architect's design for the files it calls for and hands the Developer an explicit "Required files" checklist as part of the task description - not just a check applied after the fact. If a required file is still missing once generation finishes, the next retry asks specifically for that missing file (with the rest of your codebase shown as reference), instead of either silently accepting an incomplete result or regenerating everything from scratch. This shares Targeted Single-File Retry's budget (3 attempts) and never escalates models, for the same reasons.

### 4.9 Working on an Uncommitted/In-Progress Project
No configuration needed. The Developer & Quality Gates sandbox (`.kriya/worktree`) is synced with whatever is actually on disk in your workspace before every run - including uncommitted changes and new, not-yet-`git add`ed files - not just your last commit. You don't need to commit in-progress work before running `kriya generate`; a goal that builds on or preserves existing (even uncommitted) code sees it correctly either way.

### 4.10 Knowledge Gaps and Readiness
The lesson-extraction mechanism described in 4.3 (an auto-debugging escalation staging a rule for `kriya skills approve`) captures *whether* a fix worked, but not *how usable* the resulting knowledge is - a vague, unlinked sentence and an exact dependency coordinate looked identical to Kriya before this. `kriya/knowledge/` now tags every automatically-captured fact by category (Metadata, Compatibility, Dependencies, APIs, Configuration, Rules, Examples, Verification, Constraints, Best Practices) and by how it was extracted (a direct manifest read is trusted more than an LLM guess with no supporting quote), so a skill's actual knowledge quality can be scored, not just assumed.

Two automatic sources feed this today: a repo-manifest channel that reads exact dependency coordinates already sitting in your project's own `pom.xml`/`package.json`/`requirements.txt`/`build.gradle` (mechanical, no LLM, no network - and scoped only to dependencies your skill's tags already match, so it never floods a skill with facts about libraries you're not using), and a generalized version of the lesson-extraction mechanism above (same trigger as 4.3, now producing multiple structured, category-tagged facts instead of one free sentence). Both stage into a skill folder's `staged_knowledge.json` - a new file that sits alongside, and never touches, the existing `rules.txt`/`staged_rules.txt` files - and `kriya skills approve` now promotes both in one pass.

Check how ready a skill's knowledge actually is:
```bash
kriya -c kriya.yaml skills readiness <skill_name>
```
Each category scores 0-4 (0 = nothing captured, 4 = a fact proven correct by a real run) - a skill is only as ready as its weakest category. If you don't know what's missing or how detailed an answer needs to be, ask instead of guessing:
```bash
kriya -c kriya.yaml skills gaps <skill_name>
kriya -c kriya.yaml skills gaps <skill_name> --interactive   # answer questions right here
```
This only asks about categories still below the readiness threshold - a category already scoring well stays silent, so you're never asked busywork. An interactive answer is recorded immediately (not staged for later approval) since directly answering a specific question is already a strong signal it's correct, the same trust level Kriya already gives content you supply in response to a skill-gap prompt (Section 3.4).

Doc ingestion (PDFs/README's/vendor docs) and package-registry lookups are natural next sources for this same pipeline but aren't wired up yet - see `docs/design.md` for the current state.

### 4.11 Static Pre-Checks and Best-of-N First Attempts
No configuration needed, always on: before the (expensive) compile gate runs, Kriya scans everything the Developer just wrote for a small, deliberately narrow set of known-bad patterns (`kriya/workflow/static_checks.py`) - each one added after a specific, confirmed live failure, not speculatively. If one matches, that retry attempt is redirected immediately, before ever invoking Maven/the JVM/a compiler - a false positive here just costs one more retry cycle, so each check stays conservative rather than trying to be exhaustive. Six checks run today:
- Apache Ignite's two startup mechanisms (`Ignition.start()` directly vs. an `IgniteSpringBean`) mixed in the same app - throws `IgniteException: ... already been started` at runtime.
- The same `IgniteSpringBean` Spring XML resource loaded via `ClassPathXmlApplicationContext` more than once from a single Java source file (§4.16 below) - each load auto-starts the same named Ignite node, so a second one throws the same "already been started" exception.
- An `Ignition.start()` left unclosed - hangs the JVM indefinitely after everything else already finished.
- Kriya's own `[VERIFICATION] PASS`/`FAIL` runtime-verification marker text embedded unprinted (e.g. inside a string literal instead of an actual print statement) - language-generic, scans every written file regardless of extension.
- A generated test asserting a subprocess's stdout is exactly empty, while that same entrypoint is required to print a `[VERIFICATION]` line per the verification contract - two facts that can never both be true, so the test itself (not the application) is flagged as broken.
- A `.html`/`.htm` file with no actual HTML tag anywhere in its content (matches a real opening/closing tag or `<!DOCTYPE`/comment shape, not just a bare `<` - a bare-`<` version was tried first and found to false-positive on ordinary JS comparison operators like `x < 0.000001`) - catches a sibling file's content (e.g. `script.js`) getting written under the wrong filename during a repair, the one class of project (plain HTML/CSS/JS, no recognized build manifest) where nothing else in the pipeline validates file content by language at all.

Separately, for goals where the same skill context sometimes produces a compliant first attempt and sometimes doesn't, you can ask Kriya to try more than one independent attempt before reacting to a real failure:
```yaml
autonomy:
  best_of_n_first_attempt: 2   # default 1 - today's exact single-attempt behavior
```
This only ever applies to the very first attempt of a run (not later retries, which already have real error context to react to), and is strictly sequential - never parallel - so it never uses more resources at any given moment than a normal single-attempt run; the only cost is bounded extra wall-clock in the worst case (every candidate fails). It also only activates when Kriya actually has an isolated worktree sandbox to run each candidate in (the normal case for any real git repo) - without one, a discarded candidate would have nowhere safe to reset to, so it's silently skipped rather than risk writing a failed attempt's files into your real project.

### 4.12 Fail-Closed Repair Protocol & Operation Contracts
No configuration needed, always on. Every Developer response for a create/repair attempt is checked against an explicit, executable contract before it can reach attribution logic or disk:
- **Creation** (`create_full_file`): must return complete file content; can never silently become a no-op, and can never overwrite a file that already exists under create semantics.
- **Patch repair** (`repair_with_patch`): must return at least one complete, line-anchored `SEARCH:`/`REPLACE:` pair.
- **Full-file repair** (`repair_with_full_file`): must return an explicit `FILE CONTENT:` block.
- **No-change assessment** (`no_change_assessment`): the model's `NO CHANGE NEEDED` response, contributing no write at all.

A retry-mode response missing all of these markers (ambiguous prose that isn't clearly any of the four shapes above) is rejected as malformed *before* it's ever treated as source code - closing a real gap where a local model's explanatory prose could previously get written to disk verbatim as if it were the fix. Patch and full-file repairs may safely fall back to each other, or to an explicit no-change assessment, when the target file already exists; creation has no such fallback, since a missing file silently becoming a no-op would be a much worse failure than a loud rejection. Every candidate file in a response is fully validated before any of them are written - the whole batch commits atomically as one revision-grounded unit (each file's current on-disk SHA-256 is checked against what the edit was computed against, immediately before writing), so a quality gate can never see a partially-applied response.

### 4.13 Model-Capability-Aware Generation (`llm.capabilities`)
Local models and local inference servers don't all support the same protocol features, and assuming otherwise from "it's OpenAI-compatible" alone is unreliable. `llm.capabilities` (see §2 above) makes this explicit, measured configuration instead of an inferred guess:
```yaml
llm:
  capabilities:
    native_tool_calls: true          # Model reliably supports native function/tool-calling
    json_mode: true                  # Model reliably honors a JSON-mode response_format request
    reliable_multiline_json: false   # Model can emit JSON containing embedded newlines without corrupting it
    streaming: true                  # Model/server supports streaming completions
    max_tool_argument_chars: 8192    # Native tool-call argument size ceiling before Kriya rejects it as oversized
    preferred_edit_protocol: "small_native_tools"  # or "full_file"/"full_file_text" for a model whose patch
                                                    # repairs are unreliable - forces repair_with_full_file
                                                    # instead of repair_with_patch as the safe fallback.
```
These control ordinary generation streaming, whether a JSON-mode request is even attempted, and whether Kriya prefers a small anchored patch or a full-file repair for a given model - not just native-tool-call call sites specifically. Native tools fail closed when disabled (never silently degrade to a different, unrequested protocol), and an oversized or malformed tool-call argument is rejected outright rather than truncated. The packaged defaults are correct for most modern local OpenAI-compatible servers (Ollama, LM Studio); override only for a specific model/server you've confirmed behaves differently.

### 4.14 Generation Manifest & Dependency-Aware Invalidation
No configuration needed, always on for goals where the Architect produces a real file plan. The Architect's design is converted into a stack-neutral generation manifest - every planned file tagged with a role (build/model/source/configuration/entrypoint/test/documentation/asset) and its own direct dependencies on other planned files. Generation order follows this manifest (providers before consumers - a build file before the source that needs it), rather than an arbitrary or alphabetical order.

After a real compile succeeds, Kriya records exactly which file revisions were validated together. A later edit invalidates only that specific file and its transitive dependents in the manifest - an unrelated, already-validated provider file is never needlessly regenerated just because something else in the same project changed. When a narrow (targeted) retry's budget is exhausted, broadening goes to this dependency closure first (every file that actually depends on what's currently broken) before ever paying for a full, whole-project regeneration.

### 4.15 Executable-Test Selection & Zero-Test Detection
No configuration needed, always on. A "targeted test" selected for a focused retry must be an actual executable test artifact - recognized by the filename conventions of the runners Kriya supports (pytest, Maven/Gradle Surefire, RSpec/minitest) - not merely any path that happens to sit under a directory named `test`/`tests`. A Python package initializer (`__init__.py`) or test-runner configuration file (`conftest.py`) is explicitly excluded, even though both commonly live alongside real tests.

Separately, a test run that deterministically reports **zero tests executed** (recognized across pytest, Surefire/Gradle-style "no matching tests," and other common "none collected/executed/found/ran" phrasings) is treated as its own distinct failure - `test_selection` if a bad target was chosen (triggers one immediate full-suite run, no extra LLM call needed) or `test_acceptance` if the goal explicitly required tests and none ran at all. Either way, this is never silently accepted as "the tests passed" just because nothing reported as failing - a genuinely empty test run is a failure in its own right when your goal asked for tests.

### 4.16 Lifecycle Validation Contracts
No configuration needed, always on for Java/Spring+Ignite projects specifically. This is `IgniteDuplicateSpringContextCheck`, one of §4.11's six static pre-checks, called out in its own subsection because it's a distinct *kind* of check from the others: not a syntactic pattern match, but an ecosystem lifecycle invariant checked with real XML parsing. A generated Java file that constructs `ClassPathXmlApplicationContext` against the same Spring XML resource more than once, when that resource's root bean is genuinely an `IgniteSpringBean` (the XML is actually parsed and the bean's `class` attribute inspected - not a text-mention heuristic), is rejected before compile - each construction starts the named Ignite node, so a second one throws a deterministic "already been started" exception at runtime. Comments and string-literal contents are stripped before matching (the same offset-preserving structural mirror used elsewhere in Kriya) so a documentation example or code comment mentioning the pattern can't fabricate a violation. Deliberately conservative: it only flags multiple loads within a *single* source file - one load in an entrypoint plus a separate load in a genuinely independent class (e.g. a test running in its own process) is not flagged, since without real call-graph evidence that combination would risk a false veto.

### 4.17 Standalone Planner-Artifact Validation
No configuration needed, always on. When the Planner's own draft already contains what looks like complete code for every file the Architect's design calls for (§4.8), Kriya can reuse it directly instead of a fresh Developer call. Before reusing a file this way, a conventional-artifact registry checks whether it's genuinely a complete, standalone instance of its target ecosystem file format - today's one rule: a Maven `pom.xml`'s root XML element must actually be `<project>` (namespace-insensitive). A plausible-looking fragment - e.g. a bare `<dependencies>...</dependencies>` block that happens to be well-formed XML - is retained as ordinary Planner prose but is never accepted as a real, standalone `pom.xml`; it falls through to normal Developer generation instead. The registry intentionally contains no project-specific class names, library combinations, or demo-specific strings - only standard, ecosystem-level artifact invariants.

### 4.18 Single-Presentation Reviewer Lifecycle
No configuration needed, always on. When a run needs a human approval decision, the Reviewer's report is computed once (a single completion), shown to you in full as part of that approval decision, and reused as-is for the run's own returned `review` field - it is never re-requested from the model, and the CLI never prints or re-streams it a second time at the end of the same run. An autonomous run with no approval gate shows the report once, in the final summary, exactly as before. Either way the full report is presented to you exactly once, in exactly one of those two places - if you ever previously saw what looked like the same review twice in a `generate`/`fix` run's output, that was a presentation-layer duplication (the same single result printed/logged more than once), not two separate Reviewer completions; `kriya.log`'s own escalation-reason line for an approval gate now records only a short marker ("automated code review attached") instead of the full report text, so the complete report itself only appears once in your terminal/log output too.

### 4.19 Goal Spec Compliance Gate
On by default in the packaged config (`autonomy.spec_compliance_enabled: true` in `default_config.yaml`; the pydantic model itself still defaults `false` when no config is loaded at all, e.g. a bare `AppConfig()` in a script). Compile checks, tests, and the Runtime Verification Gate (§3.4) all prove the generated code is *valid* - none of them prove it matches a CONCRETE requirement your goal literally named. If your goal states an exact field/property name, method/class name, type, or constant, and the generated code implements something different (or omits it entirely) while still compiling and passing whatever tests exist, none of the other gates can catch that - the code is syntactically correct, just the wrong shape. This runs once per attempt, only after every other gate has already passed: an LLM checks the goal's literally-named requirements against the actual generated file content and, if something concrete and named is genuinely missing, rejects the attempt with the specific missing identifier(s) named in the retry prompt. It deliberately ignores anything vague, stylistic, or left to the model's own judgment - only an exact, named requirement counts, so ordinary prose goals with no literal field/method list are unaffected and pay only the cost of one cheap completion confirming there was nothing to check. Originally shipped opt-in (off by default) purely to avoid disrupting `tests/test_workflow.py`'s existing mocked-completion sequencing, not because of any reliability concern - flipped on in the packaged default 2026-08-22 after being validated live against the actual incident that motivated building it (a goal naming 5 exact fields; the generated class had a different, incompatible set; every other gate passed anyway). Set `autonomy.spec_compliance_enabled: false` to opt back out.

---

## 5. Local Model Performance Optimization (Apple Silicon)

Optimize your local inference engine (Ollama, LM Studio, etc.) to get maximum speed out of Mixtures-of-Experts (MoE) and large reasoning models:
*   **Lock Memory (`mlock`)**: Set `OLLAMA_MLOCK=1` in your environment. This pins the model weights in your Unified Memory, avoiding swap delays when switching expert branches.
*   **Pin Thread Count**: `kriya doctor` checks directory/LLM/embedding connectivity and (if Java/Maven are present) toolchain version consistency, but it does not detect CPU cores. Check your system's physical performance-core count yourself (e.g. via `sysctl -n hw.perflevel0.physicalcpu` on Apple Silicon) and pin your inference engine's thread count to match (e.g., 8 threads for M1 Max).
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
