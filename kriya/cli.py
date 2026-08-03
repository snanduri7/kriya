import asyncio
import hashlib
import json
import logging
import os
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

import click

from kriya import __version__
from kriya.agents import ReviewerAgent
from kriya.analyzer import RepositoryAnalyzer
from kriya.config import AppConfig, load_config
from kriya.core import LLMClient
from kriya.core.kernel import Kernel
from kriya.plugins.plugin import PluginManager
from kriya.prompt import PromptEngine
from kriya.skills import SkillEngine
from kriya.workflow import WorkflowEngine

logger = logging.getLogger(__name__)

def _get_global_skills_dir() -> str:
    """Kriya's own shared, global skill library directory - not any project-local
    skills override. Extracted as its own function so tests can patch it rather than
    risk writing test data into the real shared skills on disk."""
    kriya_install_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    return os.path.join(kriya_install_dir, "skills")

def _redact_secrets(data: Any) -> Any:
    """Recursively redacts credential-carrying values before a config dump is
    ever displayed. Covers every api_key field regardless of where it's nested
    (top-level llm, llm_chain entries, each agent_llms role's own llm/llm_chain)
    without needing to enumerate each location by shape - and every value inside
    an MCP server's env dict, since those commonly carry real tokens passed to
    the MCP subprocess (key names are kept so the user can still see which env
    vars are configured, just not their values)."""
    if isinstance(data, dict):
        redacted = {}
        for key, value in data.items():
            if key == "api_key" and isinstance(value, str) and value:
                redacted[key] = "***REDACTED***"
            elif key == "env" and isinstance(value, dict):
                redacted[key] = {k: ("***REDACTED***" if v else v) for k, v in value.items()}
            else:
                redacted[key] = _redact_secrets(value)
        return redacted
    if isinstance(data, list):
        return [_redact_secrets(item) for item in data]
    return data

async def _initialize_plugins_tolerant(kernel: Kernel, pm: PluginManager) -> Dict[str, Optional[Exception]]:
    """Attempts each discovered plugin's initialize() independently, so one
    broken plugin can't prevent the others - or their tools - from becoming
    available. Deliberately not PluginManager.initialize_all(), which raises
    on the first failure and aborts before later plugins are even attempted;
    that's the right behavior for the kernel-startup call sites that keep
    using initialize_all() directly, but every command here needs to keep
    working around one bad plugin. Returns {plugin_name: None-or-exception}
    so a caller can report per-plugin status or just log failures and move on."""
    results: Dict[str, Optional[Exception]] = {}
    for p in pm.list_plugins():
        try:
            await p.initialize()
            await kernel.events.emit("plugin_initialized", {"plugin_name": p.name, "plugin": p})
            results[p.name] = None
        except Exception as e:
            logger.error(f"Plugin '{p.name}' failed to initialize, its tools will be unavailable: {e}")
            results[p.name] = e
    return results

def configure_logging(cfg: AppConfig) -> None:
    """Initializes root logging handlers (console + optional file) from AppConfig.logging."""
    if logging.getLogger().handlers:
        return

    level = getattr(logging, cfg.logging.level.upper(), logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    handlers: List[logging.Handler] = []

    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setFormatter(formatter)
    handlers.append(console_handler)

    if cfg.logging.file:
        try:
            log_path = os.path.abspath(cfg.logging.file)
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            file_handler = logging.FileHandler(log_path)
            file_handler.setFormatter(formatter)
            handlers.append(file_handler)
        except Exception as e:
            click.secho(f"Warning: Failed to initialize log file '{cfg.logging.file}': {e}", fg="yellow", err=True)

    logging.basicConfig(level=level, handlers=handlers)

@click.group(invoke_without_command=True)
@click.option('--config', '-c', type=click.Path(exists=True), help='Path to Kriya configuration YAML file.')
@click.pass_context
def main(ctx: click.Context, config: Optional[str]) -> None:
    """Kriya - Production-Grade AI Engineering Platform CLI."""
    ctx.ensure_object(dict)
    ctx.obj['config_path'] = config
    try:
        ctx.obj['config'] = load_config(config)
    except Exception as e:
        click.secho(f"Error loading configuration: {e}", fg="red", err=True)
        sys.exit(1)
    configure_logging(ctx.obj['config'])

    # No subcommand given: drop into the interactive session, same as bare
    # `python`/`node`/`claude` - but only on a real interactive terminal.
    # prompt_toolkit actively breaks on non-TTY input (garbled/hangs), and a
    # script or CI job that hits bare `kriya` by accident (a typo, a bad
    # pipeline) should fail fast with the usual help text, not hang waiting
    # on stdin forever - same TTY-safety judgment already used elsewhere in
    # this CLI (generate/fix's approval gates).
    if ctx.invoked_subcommand is None:
        if sys.stdin.isatty():
            from kriya.repl import run_repl
            run_repl(ctx.obj.get('config_path'))
        else:
            click.echo(ctx.get_help())
        ctx.exit()

@main.command()
def version() -> None:
    """Print the Kriya platform version."""
    click.echo(f"Kriya version: {__version__}")

@main.command()
@click.pass_context
def config(ctx: click.Context) -> None:
    """Display current Kriya configuration."""
    cfg: AppConfig = ctx.obj['config']
    click.echo(json.dumps(_redact_secrets(cfg.model_dump(mode="json")), indent=2))

@main.command()
@click.pass_context
def doctor(ctx: click.Context) -> None:
    """Check Kriya platform health, directories, and LLM connection."""
    cfg: AppConfig = ctx.obj['config']
    click.secho("=== Kriya Doctor ===", bold=True)
    
    # 1. Check directories
    click.echo("\nChecking directories:")
    dirs = {
        "Plugins Directory": cfg.plugins.directory,
        "Skills Directory": cfg.paths.skills,
        "Memory Directory": cfg.paths.memory,
        "Logs Directory": cfg.paths.logs,
    }
    for name, path in dirs.items():
        resolved = os.path.abspath(path)
        exists = os.path.exists(resolved)
        status = click.style("EXISTS", fg="green") if exists else click.style("MISSING (will be created on run)", fg="yellow")
        click.echo(f"  - {name}: {resolved} [{status}]")
        
    errors_found = False

    # 2. Check local LLM connection
    # NOTE: LLMClient (kriya/core/llm.py) always talks to base_url via the
    # OpenAI-compatible wire protocol regardless of what llm.provider is set
    # to - that field is documentation-only ("OpenAI-compatible client; works
    # against Ollama, LM Studio, etc.", see user_guide.md). This check must
    # always run, not be gated on provider == "openai": a user who reasonably
    # sets provider to their actual backend's name (e.g. "ollama") would
    # otherwise get the platform's core connectivity check silently skipped
    # with no indication it never ran.
    click.echo("\nChecking LLM provider connectivity:")
    provider = cfg.llm.provider
    base_url = cfg.llm.base_url
    model = cfg.llm.model
    click.echo(f"  - Provider: {provider}")
    click.echo(f"  - Base URL: {base_url}")
    click.echo(f"  - Model: {model}")
    click.echo("  - Testing connection...")
    try:
        url = f"{base_url.rstrip('/')}/models"
        req = urllib.request.Request(
            url=url,
            headers={"Authorization": f"Bearer {cfg.llm.api_key}"}
        )
        with urllib.request.urlopen(req, timeout=3.0) as response:
            status_code = response.getcode()
            content = response.read().decode('utf-8')
            data = json.loads(content)

        if status_code == 200:
            available_models = []
            if isinstance(data, dict) and "data" in data:
                available_models = [m.get("id") for m in data["data"] if isinstance(m, dict)]

            click.secho("  - [SUCCESS] Connected to local LLM server. Status code: 200", fg="green")
            if available_models:
                click.echo(f"  - Available models: {', '.join(available_models)}")
                if model in available_models:
                    click.secho(f"  - [SUCCESS] Model '{model}' is available on the server.", fg="green")
                else:
                    click.secho(f"  - [WARNING] Model '{model}' was not found in the list of available models: {available_models}", fg="yellow")
            else:
                click.echo("  - Connected successfully, but list of models was empty or couldn't be parsed.")
        else:
            click.secho(f"  - [WARNING] Connected to local server, but got status code {status_code}.", fg="yellow")
    except urllib.error.URLError as e:
        click.secho(f"  - [ERROR] Could not connect to local LLM server at {base_url}: {e.reason}", fg="red")
        click.echo("    Ensure your local LLM server (e.g. Ollama, LM Studio) is running and accessible.")
        errors_found = True
    except Exception as e:
        click.secho(f"  - [ERROR] Connectivity test encountered an error: {e}", fg="red")
        errors_found = True

    # 2.5. Check local Embedding connection
    click.echo("\nChecking Embedding provider connectivity:")
    embed_url = cfg.embedding.base_url
    embed_model = cfg.embedding.model
    click.echo(f"  - Base URL: {embed_url}")
    click.echo(f"  - Model: {embed_model}")
    click.echo("  - Testing connection...")

    try:
        from kriya.memory.vector import OllamaEmbeddingClient
        client = OllamaEmbeddingClient(base_url=embed_url, model=embed_model)
        import asyncio
        emb = asyncio.run(client.get_embedding("test connectivity"))
        if emb and any(v != 0.0 for v in emb):
            click.secho(f"  - [SUCCESS] Connected and successfully generated embedding of dimension {len(emb)}", fg="green")
        else:
            click.secho("  - [WARNING] Generated empty or zero embedding vector.", fg="yellow")
    except Exception as e:
        click.secho(f"  - [ERROR] Could not connect or failed to generate test embedding: {e}", fg="red")
        click.echo("    Ensure your embedding provider (e.g. local Ollama) is running and model is pulled.")
        errors_found = True

    # 3. Check Java/Maven toolchain resolution (only relevant if either is
    # installed - not every Kriya project is Java-based, so finding neither is
    # not an error, just skipped). 'mvn' can silently resolve a different JVM
    # than plain 'java' does (e.g. a Homebrew mvn install defaulting JAVA_HOME
    # to its own, possibly newer, openjdk) - a real, silent failure mode
    # confirmed live during golden-use-case validation: JVM startup flags
    # correct for the JDK 'java' resolves can be fatal under the JDK 'mvn'
    # actually builds/runs against, with zero indication why short of manually
    # comparing `java -version` against `mvn -version`'s own report.
    from kriya.tools.validate import check_java_toolchain
    toolchain = check_java_toolchain()
    if toolchain["java_found"] or toolchain["mvn_found"]:
        click.echo("\nChecking Java/Maven toolchain:")
        if toolchain["java_found"]:
            click.echo(f"  - 'java' resolves to JDK {toolchain['java_version']}")
        if toolchain["mvn_found"]:
            click.echo(f"  - 'mvn' will build/run against JDK {toolchain['mvn_java_version']}")
        if toolchain["mismatch"]:
            click.secho(
                f"  - [WARNING] 'java' (JDK {toolchain['java_version']}) and 'mvn' "
                f"(JDK {toolchain['mvn_java_version']}) resolve to DIFFERENT major "
                "versions. JVM startup flags tuned for one may be invalid or fatal "
                "under the other (e.g. -Djava.security.manager=allow is required on "
                "17.0.10+ but a hard VM-startup error on 24+, which removed the "
                "Security Manager entirely). Pin JAVA_HOME to the version your "
                "project targets before running `generate`/`fix` on a Java project.",
                fg="yellow"
            )
        else:
            click.secho("  - [SUCCESS] No version mismatch detected.", fg="green")

    # Optional, Java-only - a missing jdtls is never an error, just a
    # capability Kriya proceeds without (LSP grounding degrades cleanly to
    # today's behavior). Manual install only (e.g. `brew install jdtls`),
    # matching how mvn/java/Ollama are already treated - never auto-
    # downloaded. jdtls itself needs a modern JVM to RUN, separate from
    # whatever JDK the analyzed project targets - a real, previously-hit
    # pitfall elsewhere (a tool assuming "17+" here hit a silent LSP init
    # timeout in the wild) worth flagging explicitly rather than assuming
    # the reader already knows.
    from kriya.tools.lsp import JDTLS_MIN_JAVA_MAJOR_VERSION, find_jdtls
    jdtls_path = find_jdtls()
    click.echo("\nChecking optional LSP grounding (Java retry-loop diagnostics):")
    if jdtls_path:
        click.secho(f"  - [FOUND] jdtls at {jdtls_path}", fg="green")
        click.echo(
            f"    Note: jdtls itself needs JDK {JDTLS_MIN_JAVA_MAJOR_VERSION}+ to RUN - "
            "separate from whatever JDK your project targets."
        )
    else:
        click.echo("  - Not found on PATH - optional, generation proceeds without LSP grounding.")
        click.echo("    Install with `brew install jdtls` (or equivalent) to enable it.")

    if errors_found:
        click.echo()
        click.secho("One or more checks failed - see [ERROR] lines above.", fg="red", bold=True)
        sys.exit(1)

@main.command()
@click.pass_context
def repl(ctx: click.Context) -> None:
    """Start an interactive session - issue several commands in a row without
    restarting the CLI each time. Each command inside the session is the exact
    same 'kriya <command>' you'd run standalone (e.g. generate "goal" -y), just
    without the leading 'kriya' and with --config applied automatically."""
    from kriya.repl import run_repl
    run_repl(ctx.obj.get('config_path'))

@main.command()
@click.pass_context
def plugins(ctx: click.Context) -> None:
    """List loaded plugins and their lifecycle status."""
    cfg: AppConfig = ctx.obj['config']

    kernel = Kernel(config=cfg)
    pm = PluginManager(kernel=kernel, plugin_dir=cfg.plugins.directory)

    try:
        pm.discover_and_load(enabled_plugins=cfg.plugins.enabled)
        loaded = pm.list_plugins()
        if not loaded:
            click.echo("No plugins loaded.")
            return

        # Discovery (a successful __init__) only proves the plugin class exists
        # and constructs cleanly - it does NOT prove initialize() (where a plugin
        # actually registers its tools/listeners) succeeds. Every real call site
        # that uses plugins for real (tools list/execute, generate) calls
        # initialize() too, so this status command must actually attempt it as
        # well - otherwise a plugin that discovers fine but fails to initialize
        # would show here as if everything's fine while real usage breaks.
        async def run_status():
            await kernel.start()
            try:
                click.secho(f"=== Loaded Plugins ({len(loaded)}) ===", bold=True)
                results = await _initialize_plugins_tolerant(kernel, pm)
                for p in loaded:
                    err = results.get(p.name)
                    status = click.style("INITIALIZED", fg="green") if err is None else click.style(f"FAILED: {err}", fg="red")
                    click.echo(f"  - {p.name} (v{p.version}) [{status}]")
                return results
            finally:
                await pm.shutdown_all()
                await kernel.stop()

        results = asyncio.run(run_status())

        if any(err is not None for err in results.values()):
            sys.exit(1)
    except Exception as e:
        click.secho(f"Error loading plugins: {e}", fg="red")
        sys.exit(1)

@main.group(name="prompt")
def prompt_group() -> None:
    """Manage and render prompt templates."""
    pass

@prompt_group.command(name="render")
@click.argument('template_name')
@click.option('--var', '-v', multiple=True, help="Variables to pass to template in key=value format.")
@click.option('--template-dir', '-t', type=click.Path(exists=True, file_okay=False, dir_okay=True), help="Directory containing custom '<name>.jinja' templates, checked before the 4 built-in defaults (system_instructions, code_review, refactor, generate_code).")
def prompt_render(template_name: str, var: tuple, template_dir: Optional[str]) -> None:
    """Render a prompt template with variables."""
    vars_dict = {}
    for variable in var:
        if '=' in variable:
            k, v = variable.split('=', 1)
            vars_dict[k.strip()] = v.strip()
        else:
            click.secho(f"Invalid variable format '{variable}'. Expected 'key=value'.", fg="red")
            sys.exit(1)

    # PromptEngine has always supported a template_dir constructor arg for
    # custom '<name>.jinja' files (checked before the 4 built-in defaults),
    # but this was the only call site in the whole codebase and it never
    # passed one - confirmed via grep, and live: a real custom .jinja file on
    # disk was unreachable no matter what, with zero indication why. --var
    # already exists as a per-invocation option for this same command, so a
    # matching --template-dir option is the minimal fix that actually exposes
    # the existing capability, rather than a new persistent config field.
    pe = PromptEngine(template_dir=template_dir)
    try:
        rendered = pe.render(template_name, vars_dict)
        click.echo(rendered)
    except Exception as e:
        click.secho(f"Error: {e}", fg="red")
        sys.exit(1)

@prompt_group.command(name="generate")
@click.argument('description')
@click.pass_context
def prompt_generate(ctx: click.Context, description: str) -> None:
    """Generate an optimized code-generation prompt based on a high-level description.

    Prints the prompt to stdout and auto-saves it to .kriya/last_prompt.md,
    so you can review it before actually building anything:

    \b
      kriya prompt generate "a REST API for managing a todo list"
      kriya generate --file .kriya/last_prompt.md -y
    """
    cfg: AppConfig = ctx.obj['config']
    
    import asyncio

    from kriya.core.llm import LLMClient
    llm = LLMClient(cfg)
    
    system_prompt = (
        "You are an expert software developer and prompt engineer.\n"
        "Your task is to take a high-level description of an application or feature, and "
        "generate a highly optimized, precise, and structured prompt that can be used by an AI coding assistant "
        "(like Kriya) to generate the application correctly.\n"
        "The generated prompt should specify:\n"
        "- The exact technology stack and recommended versions.\n"
        "- The system architecture and files structure.\n"
        "- Edge cases, compiler configuration, and required quality gates / unit tests.\n"
        "- Directives to avoid common hallucinations.\n"
        "Output ONLY the optimized prompt itself, formatted cleanly in Markdown. Do not include introductory or concluding conversational filler."
    )
    
    async def run_gen():
        click.secho("Generating optimized prompt...", fg="cyan", err=True)
        return await llm.complete(
            system_prompt=system_prompt,
            user_prompt=f"Create a developer prompt for: {description}"
        )

    try:
        res = asyncio.run(run_gen())
        # Status/header chrome goes to stderr, err=True - stdout carries ONLY
        # the generated prompt text, so `kriya prompt generate "x" | kriya
        # generate -y` (real shell piping, standalone CLI use) gets a clean
        # goal rather than the two lines above mixed into it. Verified live
        # before this fix: both lines appeared in stdout, which would have
        # been fed to `generate` as if they were part of the actual goal.
        click.secho("\n=== GENERATED DEVELOPER PROMPT ===\n", bold=True, fg="green", err=True)
        click.echo(res)

        # Inside `kriya repl` there's no shell pipe between two typed lines -
        # each dispatches independently and control returns to the prompt
        # afterward - so the stdout/stderr split above doesn't help a REPL
        # user chain this into a follow-up `generate` call. Auto-save to a
        # fixed, predictable path and reuse generate's EXISTING --file flag
        # (no new mechanism) rather than inventing REPL-specific reference
        # syntax for what is, deliberately, a single concrete use case.
        try:
            kriya_dir = os.path.join(os.getcwd(), ".kriya")
            os.makedirs(kriya_dir, exist_ok=True)
            saved_path = os.path.join(kriya_dir, "last_prompt.md")
            with open(saved_path, "w", encoding="utf-8") as f:
                f.write(res)
            click.secho(
                f"\nSaved to {os.path.relpath(saved_path)} - run: generate --file {os.path.relpath(saved_path)} -y",
                fg="blue", dim=True, err=True,
            )
        except Exception as e:
            # Don't claim a save that didn't happen - warn instead of silently
            # printing nothing, so the user isn't left wondering why the hint
            # they expected never showed up.
            click.secho(f"\n[WARNING] Could not auto-save the generated prompt to .kriya/last_prompt.md: {e}", fg="yellow", err=True)
    except Exception as e:
        click.secho(f"Failed to generate prompt: {e}", fg="red")
        sys.exit(1)

@main.group(name="tools")
def tools_group() -> None:
    """Manage and execute platform tools."""
    pass

@tools_group.command(name="list")
@click.pass_context
def tools_list(ctx: click.Context) -> None:
    """List all registered tools and their arguments schema."""
    cfg: AppConfig = ctx.parent.obj['config'] if ctx.parent else load_config()
    
    kernel = Kernel(config=cfg)
    pm = PluginManager(kernel=kernel, plugin_dir=cfg.plugins.directory)
    
    try:
        pm.discover_and_load(enabled_plugins=cfg.plugins.enabled)

        async def run_list():
            await kernel.start()
            try:
                # Tolerant, not initialize_all(): a plugin that fails to
                # initialize must not prevent every OTHER plugin's (or native/
                # MCP) tools from being listed - confirmed live with a
                # deliberately broken test plugin that this previously took
                # down tool listing entirely, not just its own tools.
                init_results = await _initialize_plugins_tolerant(kernel, pm)
                failed = [name for name, err in init_results.items() if err is not None]
                if failed:
                    click.secho(f"[WARNING] {len(failed)} plugin(s) failed to initialize - their tools are unavailable: {', '.join(failed)}", fg="yellow")

                tools = kernel.registry.list_components("tool")
                if not tools:
                    click.echo("No tools registered.")
                    return

                click.secho(f"=== Registered Tools ({len(tools)}) ===", bold=True)
                for tool_name in tools:
                    tool = kernel.registry.get("tool", tool_name)
                    click.secho(f"\n  - {tool.name}", bold=True, fg="cyan")
                    click.echo(f"    Description: {tool.description}")
                    click.echo(f"    Schema: {json.dumps(tool.arguments_schema.model_json_schema(), indent=4)}")
            finally:
                # try/finally, not calls at the end of the happy path: the
                # "No tools registered" early return and any exception raised
                # while listing tools used to skip this entirely, leaking any
                # real MCP subprocess servers kernel.start() spawned (they are
                # not reaped when this process exits - only kernel.stop() ->
                # MCPManager.shutdown_all() terminates them).
                await pm.shutdown_all()
                await kernel.stop()

        asyncio.run(run_list())
    except Exception as e:
        click.secho(f"Error: {e}", fg="red")
        sys.exit(1)

@tools_group.command(name="execute")
@click.argument('tool_name')
@click.argument('arguments_json', required=False)
@click.option('--yes', '-y', is_flag=True, help="Auto-approve execution of tools that require confirmation (e.g. shell).")
@click.pass_context
def tools_execute(ctx: click.Context, tool_name: str, arguments_json: Optional[str], yes: bool) -> None:
    """Execute a tool with JSON arguments."""
    cfg: AppConfig = ctx.parent.obj['config'] if ctx.parent else load_config()

    kernel = Kernel(config=cfg)
    pm = PluginManager(kernel=kernel, plugin_dir=cfg.plugins.directory)

    try:
        pm.discover_and_load(enabled_plugins=cfg.plugins.enabled)

        async def run_exec():
            await kernel.start()
            try:
                # Tolerant, not initialize_all(): an unrelated plugin failing
                # to initialize must not block execution of a specific,
                # explicitly-named tool that loaded fine.
                await _initialize_plugins_tolerant(kernel, pm)

                try:
                    tool = kernel.registry.get("tool", tool_name)
                except Exception as e:
                    logger.debug(f"Failed to resolve tool '{tool_name}': {e}")
                    click.secho(f"Tool '{tool_name}' not found.", fg="red")
                    sys.exit(1)

                args = json.loads(arguments_json) if arguments_json else {}

                if tool.requires_confirmation and not yes:
                    click.secho(f"\n[CONFIRMATION REQUIRED] Tool '{tool_name}' requires approval before execution.", fg="yellow")
                    click.echo(f"Arguments: {json.dumps(args, indent=2)}")
                    if not sys.stdin.isatty():
                        click.secho("Error: Non-TTY (piped) input detected. You must specify the '--yes' (-y) flag to auto-approve.", fg="red")
                        sys.exit(1)
                    if not click.confirm("Proceed with execution?"):
                        click.secho("Execution cancelled.", fg="yellow")
                        sys.exit(1)

                try:
                    result = await tool.execute(**args)
                    if isinstance(result, (dict, list)):
                        click.echo(json.dumps(result, indent=2))
                    else:
                        click.echo(result)
                except Exception as ex:
                    click.secho(f"Execution failed: {ex}", fg="red")
            finally:
                # try/finally so a bad --yes-less invalid-JSON arguments_json,
                # or any other exception before reaching the end of the happy
                # path, still terminates any real MCP subprocess servers
                # kernel.start() spawned - see the matching note in tools_list.
                await pm.shutdown_all()
                await kernel.stop()

        asyncio.run(run_exec())
    except Exception as e:
        click.secho(f"Execution failed: {e}", fg="red")
        sys.exit(1)

@main.command()
@click.argument('path', type=click.Path(exists=True))
@click.option('--changed', '-d', is_flag=True, default=False, help="Only index files changed in git.")
@click.option('--force', '-f', is_flag=True, default=False, help="Force complete re-indexing.")
@click.pass_context
def analyze(ctx: click.Context, path: str, changed: bool, force: bool) -> None:
    """Analyze and index a repository directory."""
    cfg: AppConfig = ctx.obj['config']
    # click.Path(exists=True) accepts a file too, but analyze() walks path as a
    # directory (os.walk on a file path silently yields nothing) - confirmed
    # live: pointing analyze at a single file produced an empty-looking but
    # "successful" analysis (languages: {}, total_files_indexed: 0) with no
    # error or indication that a directory was actually required, despite the
    # command's own docstring saying exactly that.
    if not os.path.isdir(path):
        click.secho(f"Error: '{path}' is a file, not a directory - analyze requires a repository directory.", fg="red", err=True)
        sys.exit(1)
    analyzer = RepositoryAnalyzer(path)
    try:
        model = analyzer.analyze()
        # The JSON payload is the only thing that belongs on stdout - everything
        # from here down is progress/status narration and goes to stderr
        # (err=True / file=sys.stderr), so `kriya analyze . | jq .` (or any
        # other downstream JSON consumer) doesn't choke on trailing chrome
        # printed after the closing brace. Confirmed live before this fix: the
        # index-building messages and progress bar were plain stdout output,
        # appended right after the JSON on the same stream.
        click.echo(model.model_dump_json(indent=2))

        if os.path.isdir(path):
            click.secho("\nBuilding semantic repository index...", bold=True, fg="cyan", err=True)

            progress_bar = None

            def progress_callback(filepath: str, idx: int, total: int):
                nonlocal progress_bar
                if progress_bar is None:
                    progress_bar = click.progressbar(length=total, label="Indexing repository files", file=sys.stderr)

                # Check if it is a skip or compile
                if "[Up-to-date]" in filepath:
                    label_text = f"Skipping: {filepath[:30]}"
                else:
                    label_text = f"Indexing: {filepath[:30]}"

                progress_bar.label = label_text.ljust(45)
                progress_bar.update(1)

            async def run_indexing():
                await analyzer.index_repository(cfg, changed=changed, force=force, progress_callback=progress_callback)

            asyncio.run(run_indexing())
            if progress_bar:
                progress_bar.render_finish()
            click.secho("Success: Semantic index compiled and cached to disk.", fg="green", err=True)
    except Exception as e:
        click.secho(f"Analysis failed: {e}", fg="red", err=True)
        sys.exit(1)

@main.group(name="skills")
def skills_group() -> None:
    """Manage platform engineering skills."""
    pass

@skills_group.command(name="list")
@click.pass_context
def skills_list(ctx: click.Context) -> None:
    """List all registered skills and staged/active conventions."""
    cfg: AppConfig = ctx.parent.obj['config'] if ctx.parent else load_config()
    se = SkillEngine(cfg.paths.skills)
    se.discover_and_load()
    
    skills = se.list_skills()
    if not skills:
        click.echo("No skills discovered. You can create one with 'kriya skills create <name>'.")
        return
        
    click.secho(f"=== Discovered Skills ({len(skills)}) ===", bold=True)
    for s in skills:
        if s.verified:
            provenance = []
            if s.verified_context:
                provenance.append(s.verified_context)
            if s.verified_at:
                provenance.append(f"on {s.verified_at}")
            verified_str = " [VERIFIED" + (f" - {', '.join(provenance)}" if provenance else "") + "]"
            verified_fg = "green"
        else:
            verified_str = " [UNVERIFIED]"
            verified_fg = "yellow"
        click.echo(f"  - {s.name}: {s.description} [Category: {s.category}]", nl=False)
        click.secho(verified_str, fg=verified_fg)
        staged_file = os.path.join(cfg.paths.skills, s.name.lower(), "staged_rules.txt")
        if not os.path.exists(staged_file):
            staged_file = os.path.join(cfg.paths.skills, s.name, "staged_rules.txt")
        if os.path.exists(staged_file):
            try:
                with open(staged_file, "r", encoding="utf-8") as sf:
                    lines = [line.strip() for line in sf if line.strip()]
                if lines:
                    click.secho(f"    [STAGED RULES PENDING REVIEW ({len(lines)})]:", fg="yellow")
                    for line in lines:
                        click.echo(f"      * {line}")
            except Exception as e:
                logger.debug(f"Failed to read staged rules file '{staged_file}': {e}")

@skills_group.command(name="show")
@click.argument('skill_name')
@click.pass_context
def skills_show(ctx: click.Context, skill_name: str) -> None:
    """Display information about a specific skill."""
    cfg: AppConfig = ctx.parent.obj['config'] if ctx.parent else load_config()
    se = SkillEngine(cfg.paths.skills)
    se.discover_and_load()
    
    try:
        s = se.get_skill(skill_name)
        click.secho(f"=== Skill: {s.name} ===", bold=True, fg="cyan")
        click.echo(f"Description: {s.description}")
        click.echo(f"Category:    {s.category}")
        click.echo(f"Tags:        {', '.join(s.tags)}")
        if s.verified:
            click.secho(f"Verified:    yes (context: {s.verified_context or 'unspecified'}, on {s.verified_at or 'unknown date'})", fg="green")
            click.echo("             Run 'kriya skills unverify " + skill_name + "' if you believe this is now stale.")
        else:
            click.secho("Verified:    no - future generation runs will be asked to strengthen this skill.", fg="yellow")


        if s.rules:
            click.secho("\nRules:", bold=True)
            unverified_texts = set()
            if s.source_path:
                from kriya.skills.skill import load_rule_provenance
                unverified_texts = {
                    p["text"] for p in load_rule_provenance(s.source_path) if not p.get("verified", False)
                }
            for r in s.rules:
                if r in unverified_texts:
                    click.secho(f"  - {r} ", fg="yellow", nl=False)
                    click.secho("[unverified]", fg="yellow", dim=True)
                else:
                    click.echo(f"  - {r}")

        if s.instructions:
            click.secho("\nInstructions:", bold=True)
            click.echo(s.instructions)
            
        if s.examples:
            click.secho("\nExamples:", bold=True)
            for name, content in s.examples.items():
                click.secho(f"  [{name}]", bold=True, fg="yellow")
                click.echo(content)
    except KeyError:
        click.secho(f"Skill '{skill_name}' not found.", fg="red")
        sys.exit(1)

@skills_group.command(name="create")
@click.argument('skill_name')
@click.pass_context
def skills_create(ctx: click.Context, skill_name: str) -> None:
    """Create a default skeleton for a new skill."""
    cfg: AppConfig = ctx.parent.obj['config'] if ctx.parent else load_config()

    from kriya.skills.skill import is_accidental_shared_skills_write
    if is_accidental_shared_skills_write(cfg.paths.skills, os.getcwd()):
        click.secho(
            f"Warning: this project's config doesn't set paths.skills, so '{skill_name}' is about "
            f"to be created in Kriya's own SHARED install skills directory "
            f"({os.path.abspath(cfg.paths.skills)}) instead of a project-local one - every other "
            f"project using Kriya would inherit it. If that's not intended, stop now (Ctrl+C) and "
            f"set paths.skills in this project's kriya.yaml, e.g. \"./skills\".",
            fg="red", bold=True,
        )

    os.makedirs(cfg.paths.skills, exist_ok=True)

    se = SkillEngine(cfg.paths.skills)
    try:
        path = se.create_skill_skeleton(skill_name)
        click.secho(f"Successfully created skill skeleton at: {path}", fg="green")
    except Exception as e:
        click.secho(f"Failed to create skill: {e}", fg="red")
        sys.exit(1)

@skills_group.command(name="approve")
@click.argument('skill_name')
@click.pass_context
def skills_approve(ctx: click.Context, skill_name: str) -> None:
    """Approve all staged rules for a specific skill, promoting them to rules.txt."""
    cfg: AppConfig = ctx.parent.obj['config'] if ctx.parent else load_config()
    skill_folder = os.path.join(cfg.paths.skills, skill_name.lower())
    if not os.path.exists(skill_folder):
        skill_folder = os.path.join(cfg.paths.skills, skill_name)
    staged_file = os.path.join(skill_folder, "staged_rules.txt")
    rules_file = os.path.join(skill_folder, "rules.txt")
    
    if not os.path.exists(staged_file):
        click.secho(f"No staged rules found for skill '{skill_name}'.", fg="yellow")
        return
        
    try:
        with open(staged_file, "r", encoding="utf-8") as sf:
            staged_lines = [line.strip() for line in sf if line.strip()]
            
        if not staged_lines:
            click.secho(f"No staged rules found for skill '{skill_name}'.", fg="yellow")
            return
            
        with open(rules_file, "a", encoding="utf-8") as rf:
            for line in staged_lines:
                rf.write(f"\n{line}")
                
        os.remove(staged_file)
        click.secho(f"Successfully approved and promoted {len(staged_lines)} rule(s) to rules.txt for skill '{skill_name}'!", fg="green")
    except Exception as e:
        click.secho(f"Failed to approve staged rules: {e}", fg="red")

@skills_group.command(name="promote")
@click.argument('source_skill')
@click.argument('target_skill')
@click.option('--rule', help="Promote exactly this one rule (must already be an approved rule in the source skill's rules.txt).")
@click.option('--all', 'promote_all', is_flag=True, help="Promote every approved rule in the source skill not already present in the target.")
@click.pass_context
def skills_promote(ctx: click.Context, source_skill: str, target_skill: str, rule: Optional[str], promote_all: bool) -> None:
    """Promote a validated, repo-local lesson into a shared, reusable skill.

    Lesson extraction (kriya generate's auto-debugging loop) and 'kriya skills approve'
    only ever affect a single repo's private auto-<repo-slug> skill - the same mistake
    has to be independently rediscovered in every other project that uses the same
    technology. This promotes an already-approved rule from SOURCE_SKILL up into
    TARGET_SKILL in Kriya's shared skill library, so every future project benefits.

    Deliberately requires interactive confirmation with no --yes bypass, even under
    'generate -y' - promoting into a shared skill affects every future project that
    uses it, not just the current one, and should never happen unattended.
    """
    if not rule and not promote_all:
        click.secho("Specify either --rule \"<exact text>\" or --all.", fg="red")
        sys.exit(1)
    if rule and promote_all:
        click.secho("Specify only one of --rule or --all, not both.", fg="red")
        sys.exit(1)

    cfg: AppConfig = ctx.parent.obj['config'] if ctx.parent else load_config()

    # Source: the project-local skills dir (cfg.paths.skills), typically where an
    # auto-<repo-slug> skill's already-human-approved rules.txt lives.
    source_folder = os.path.join(cfg.paths.skills, source_skill.lower())
    if not os.path.exists(source_folder):
        source_folder = os.path.join(cfg.paths.skills, source_skill)
    source_rules_file = os.path.join(source_folder, "rules.txt")
    if not os.path.exists(source_rules_file):
        click.secho(f"No approved rules.txt found for source skill '{source_skill}' at {source_rules_file}.", fg="red")
        sys.exit(1)

    with open(source_rules_file, "r", encoding="utf-8") as sf:
        source_rules = [line.strip() for line in sf if line.strip()]
    if not source_rules:
        click.secho(f"Source skill '{source_skill}' has no approved rules to promote.", fg="yellow")
        return

    # Target: always Kriya's own shared, global skill library - not whatever
    # project-local skills dir happens to be active for this invocation - promotion
    # is meant to benefit every future project, not just the current one.
    global_skills_dir = _get_global_skills_dir()
    target_folder = os.path.join(global_skills_dir, target_skill.lower())
    if not os.path.exists(target_folder):
        target_folder = os.path.join(global_skills_dir, target_skill)
    if not os.path.exists(target_folder):
        click.secho(
            f"Target skill '{target_skill}' does not exist in the shared skill library at {global_skills_dir}. "
            f"Use 'kriya skills create {target_skill}' first if this should be a new shared skill.",
            fg="red"
        )
        sys.exit(1)
    target_rules_file = os.path.join(target_folder, "rules.txt")

    existing_target_rules = []
    if os.path.exists(target_rules_file):
        with open(target_rules_file, "r", encoding="utf-8") as tf:
            existing_target_rules = [line.strip() for line in tf if line.strip()]

    if rule:
        if rule not in source_rules:
            click.secho(f"Rule not found in '{source_skill}'s approved rules.txt: {rule}", fg="red")
            sys.exit(1)
        candidates = [rule]
    else:
        candidates = source_rules

    to_promote = [r for r in candidates if r not in existing_target_rules]
    if not to_promote:
        click.secho("Nothing to promote - the specified rule(s) are already present in the target skill.", fg="yellow")
        return

    click.secho(f"\nAbout to promote {len(to_promote)} rule(s) from '{source_skill}' into the SHARED skill '{target_skill}':", bold=True, fg="yellow")
    click.echo(f"  Target: {target_rules_file}")
    for r in to_promote:
        click.echo(f"  + {r}")
    click.secho("\nThis affects every future project that uses this skill, not just the current one.", fg="yellow")
    if not click.confirm("Proceed?"):
        click.secho("Aborted.", fg="red")
        return

    with open(target_rules_file, "a", encoding="utf-8") as tf:
        for r in to_promote:
            tf.write(f"\n{r}")
    from kriya.skills.skill import git_commit_if_tracked
    git_commit_if_tracked(target_rules_file, f"Kriya: promote {len(to_promote)} rule(s) from '{source_skill}' into skill '{target_skill}'")

    # A human explicitly vouching for a rule is at least as strong a trust signal as an
    # automated passing Runtime Verification run - mark the target skill verified too,
    # so future generations stop being asked to strengthen it.
    target_engine = SkillEngine(global_skills_dir, load_global=False)
    target_engine.discover_and_load()
    target_engine.mark_verified(target_skill, context=f"promoted from '{source_skill}'")

    click.secho(f"Successfully promoted {len(to_promote)} rule(s) into shared skill '{target_skill}'.", fg="green")

@skills_group.command(name="unverify")
@click.argument('skill_name')
@click.pass_context
def skills_unverify(ctx: click.Context, skill_name: str) -> None:
    """Resets a skill's verified status, so future generation runs are asked to
    strengthen/re-confirm it again.

    Deliberately manual - a failing Runtime Verification run never automatically
    demotes a previously-verified skill, since attributing a failure to one specific
    skill among several active ones is unreliable. Use this when you know a skill has
    gone stale for any reason (a pinned version got yanked, a new major version
    changed the config shape, an approach became deprecated) - check 'kriya skills
    show <name>' first to see when/what it was last verified for.
    """
    cfg: AppConfig = ctx.parent.obj['config'] if ctx.parent else load_config()
    se = SkillEngine(cfg.paths.skills)
    se.discover_and_load()

    try:
        skill = se.get_skill(skill_name)
    except KeyError:
        click.secho(f"Skill '{skill_name}' not found.", fg="red")
        sys.exit(1)

    if not skill.verified and not skill.verification_gap_acknowledged:
        click.secho(f"Skill '{skill_name}' is already unverified.", fg="yellow")
        return

    if se.mark_unverified(skill_name):
        click.secho(f"Skill '{skill_name}' reset to unverified - future generation runs will be asked to strengthen it again.", fg="green")
    else:
        click.secho(f"Failed to update skill '{skill_name}'.", fg="red")
        sys.exit(1)

@main.command()
@click.argument('goal', required=False)
@click.option('--file', '-f', type=click.Path(exists=True), help="Path to a text/markdown file containing the goal/prompt.")
@click.option('--yes', '-y', is_flag=True, default=False, help="Auto-approve all proposed code changes.")
@click.option('--knowledge-policy', type=click.Choice(['strict', 'warn', 'permissive']), default='warn', help="KnowledgeGuard policy for handling detected gaps.")
@click.option('--ack-knowledge-gap', multiple=True, help="Acknowledge specific coordinates (e.g. org.apache.ignite:ignite-core) to bypass check.")
@click.option('--resume', is_flag=True, default=False, help="Resume the most recently saved checkpoint for this workspace (only exists if a prior run was interrupted mid-Plan/Design/Developer).")
@click.option('--resume-id', default=None, help="Resume a specific checkpoint by run_id instead of the latest one.")
@click.pass_context
def generate(ctx: click.Context, goal: Optional[str], file: Optional[str], yes: bool, knowledge_policy: str, ack_knowledge_gap: tuple, resume: bool, resume_id: Optional[str]) -> None:
    """Run autonomous multi-agent pipeline to satisfy a goal."""
    if file:
        try:
            with open(file, "r", encoding="utf-8") as fh:
                goal = fh.read()
        except Exception as e:
            click.secho(f"Failed to read goal file: {e}", fg="red")
            sys.exit(1)
    elif not goal:
        if not sys.stdin.isatty():
            goal = sys.stdin.read()
        else:
            click.secho("Error: Missing argument 'GOAL' or '--file' option.", fg="red")
            sys.exit(1)

    cfg: AppConfig = ctx.obj['config']
    
    llm = LLMClient(cfg)
    kernel = Kernel(config=cfg)
    we = WorkflowEngine(kernel, llm)
    
    current_step = None

    def on_stream(step_name: str, token: str):
        nonlocal current_step
        if current_step != step_name:
            current_step = step_name
            click.secho(f"\n>>> Step: {step_name} <<<", bold=True, fg="green")
        click.echo(token, nl=False)
        sys.stdout.flush()

    def on_approval(files: List[Dict[str, str]], reason: str) -> bool:
        if yes:
            click.secho(f"\n[Auto-Approving] Reason: {reason}", bold=True, fg="green")
            return True
            
        click.secho(f"\n[Escalation Review Needed] Reason: {reason}", bold=True, fg="yellow")
        for f in files:
            filepath = f.get("filepath", "")
            content = f.get("content", "")
            click.secho(f"\n--- Proposed changes for: {filepath} ---", bold=True, fg="cyan")
            lines = content.splitlines()
            click.echo("\n".join(lines[:15]))
            if len(lines) > 15:
                click.echo(f"... and {len(lines) - 15} more lines.")
        return click.confirm("\nDo you approve applying these changes to the codebase?")

    def on_skill_gap(reason: str, skill_names: List[str]) -> Optional[str]:
        if yes:
            click.secho(f"\n[Auto-Skipping Skill Gap Check] {reason}", dim=True)
            return None

        click.secho(f"\n[Skill Gap Detected] {reason}", bold=True, fg="yellow")
        supplied = click.prompt(
            "Provide a URL, file path, or paste reference text to strengthen it "
            "(leave blank to proceed anyway with best-effort generation)",
            default="", show_default=False
        )
        return supplied.strip() or None

    def on_skill_conflict(skill_a: str, rule_a: str, skill_b: str, rule_b: str, explanation: str) -> Optional[str]:
        if yes:
            click.secho(f"\n[Auto-Skipping Skill Conflict Check] '{skill_a}' vs '{skill_b}'", dim=True)
            return None

        click.secho(f"\n[Possible Skill Conflict] '{skill_a}' and '{skill_b}' are both active for this run:", bold=True, fg="yellow")
        click.echo(f"  [{skill_a}] {rule_a}")
        click.echo(f"  [{skill_b}] {rule_b}")
        if explanation:
            click.echo(f"  Why: {explanation}")
        choice = click.prompt(
            "Which rule should govern this generation? "
            "(a = prefer skill A's rule, b = prefer skill B's rule, both = not actually conflicting)",
            type=click.Choice(["a", "b", "both"]), default="both"
        )
        click.secho("This decision will be remembered for future runs of these two skills.", dim=True)
        return {"a": "prefer_a", "b": "prefer_b", "both": "both_ok"}[choice]

    def on_web_lookup(found: List[Dict[str, str]]) -> bool:
        if yes:
            click.secho(f"\n[Auto-Skipping Live Lookup Review] found {len(found)} reference(s), discarding under -y", dim=True)
            return False

        click.secho(f"\n[Live Lookup] Found {len(found)} reference(s) to strengthen skill coverage for this run:", bold=True, fg="yellow")
        for item in found:
            click.echo(f"  [{item['term']}] {item['url']}")
            if item.get("snippet"):
                click.echo(f"    {item['snippet'][:160]}")
        return click.confirm("\nUse these references for this run? (declining discards all of them, none partially)")

    def on_web_lookup_query(terms: List[str], base_url: str) -> bool:
        # Only ever called when autonomy.web_lookup_auto_approve is False -
        # WorkflowEngine._approve_web_lookup() short-circuits to True without
        # reaching this callback at all when that opt-in is set.
        if yes:
            click.secho(
                f"\n[Auto-Skipping Live Lookup] would search for {terms} via {base_url} - skipped under "
                "-y (set autonomy.web_lookup_auto_approve: true to allow this unattended)",
                dim=True
            )
            return False

        click.secho("\n[Live Lookup] Kriya wants to search for reference material on:", bold=True, fg="yellow")
        for t in terms:
            click.echo(f"  - {t}")
        click.echo(f"via: {base_url}")
        return click.confirm(
            "\nSend this search? (only these bare technology-name terms are sent - "
            "never goal/design/code/error text)"
        )

    async def run_workflow():
        nonlocal goal
        rag_context = ""
        index_path = os.path.join(cfg.paths.memory, "web_knowledge.db")
        if os.path.exists(index_path):
            try:
                from kriya.memory.vector import LocalVectorStore, OllamaEmbeddingClient
                embed_client = OllamaEmbeddingClient(
                    base_url=cfg.embedding.base_url,
                    model=cfg.embedding.model
                )
                vector_store = LocalVectorStore(index_path)
                query_emb = await embed_client.get_embedding(goal, is_query=True)
                matches = vector_store.query(query_emb, top_k=5)
                good_matches = [m for m in matches if m["score"] > 0.4]
                if good_matches:
                    rag_context = "\n".join([f"[Source: {m['filepath']}]\n{m['text']}" for m in good_matches])
                vector_store.close()
            except Exception as e:
                logger.warning(f"Failed to query RAG database in workflow: {e}")
                
        if rag_context:
            goal = f"{goal}\n\n=== Web Reference Documentation Context ===\n{rag_context}"

        await kernel.start()
        res = await we.run_generation_workflow(
            goal=goal,
            workspace_path=os.getcwd(),
            approval_callback=on_approval,
            stream_callback=on_stream,
            skill_gap_callback=on_skill_gap,
            skill_conflict_callback=on_skill_conflict,
            web_lookup_callback=on_web_lookup,
            web_lookup_query_callback=on_web_lookup_query,
            resume=resume,
            resume_id=resume_id
        )

        if isinstance(res, dict) and res.get("status") == "knowledge_gap":
            gap_report_dict = res["gap_report"]
            unacked_gaps = []
            for g in gap_report_dict.get("gaps", []):
                coord = g["library"]
                if coord not in ack_knowledge_gap:
                    unacked_gaps.append(g)

            if not unacked_gaps or knowledge_policy == 'permissive':
                res = await we.run_generation_workflow(
                    goal=goal,
                    workspace_path=os.getcwd(),
                    approval_callback=on_approval,
                    stream_callback=on_stream,
                    skill_gap_callback=on_skill_gap,
                    skill_conflict_callback=on_skill_conflict,
                    web_lookup_callback=on_web_lookup,
                    web_lookup_query_callback=on_web_lookup_query,
                    knowledge_risk_confirmed=True,
                    resume=resume,
                    resume_id=resume_id
                )
            elif knowledge_policy == 'strict':
                click.secho("\n[KRIYA BLOCKED] Knowledge gap detected in strict mode:", bold=True, fg="red")
                for g in unacked_gaps:
                    version_desc = "no specific version mentioned" if g['version'] == "unspecified" else f"version {g['version']}"
                    click.secho(f"  - {g['library']} ({version_desc}): {g['reason']}", fg="red")
                await kernel.stop()
                sys.exit(1)
            else:  # 'warn'
                click.secho("\n⚠️  KNOWLEDGE GUARD RISK DETECTED", bold=True, fg="yellow")
                for g in unacked_gaps:
                    if g['version'] == "unspecified":
                        click.secho(f"  Library  : {g['library']} (no specific version mentioned)", fg="yellow")
                        click.secho(f"  Risk Level: {g['risk_level']}", fg="yellow")
                    else:
                        date_str = g["release_date"][:10] if g["release_date"] else "Unknown"
                        click.secho(f"  Library  : {g['library']} (version {g['version']})", fg="yellow")
                        click.secho(f"  Released : {date_str}  |  Risk Level: {g['risk_level']}", fg="yellow")
                    click.secho(f"  Reason   : {g['reason']}", fg="yellow")
                    click.echo("")

                if yes:
                    click.secho("[Auto-Confirming knowledge risk]", fg="green")
                    confirm = True
                else:
                    confirm = click.confirm("Do you want to proceed anyway despite the knowledge risk?")

                if confirm:
                    res = await we.run_generation_workflow(
                        goal=goal,
                        workspace_path=os.getcwd(),
                        approval_callback=on_approval,
                        stream_callback=on_stream,
                        skill_gap_callback=on_skill_gap,
                        skill_conflict_callback=on_skill_conflict,
                        web_lookup_callback=on_web_lookup,
                        web_lookup_query_callback=on_web_lookup_query,
                        knowledge_risk_confirmed=True,
                        resume=resume,
                        resume_id=resume_id
                    )
                else:
                    if click.confirm("Would you like Kriya to scaffold skill templates for these libraries?"):
                        from kriya.tools.knowledge import KnowledgeGuard
                        knowledge_config = cfg.knowledge
                        cutoff = cfg.llm.knowledge_cutoff
                        if knowledge_config.training_cutoff != "2023-12-01":
                            cutoff = knowledge_config.training_cutoff
                        guard = KnowledgeGuard(
                            skills_dir=cfg.paths.skills,
                            cutoff_date_str=cutoff,
                            offline=knowledge_config.offline_mode
                        )
                        for g in unacked_gaps:
                            t_dir = guard.generate_skill_template(g["library"], g["version"])
                            click.secho(f"Created skill template at: {t_dir}", fg="green")
                        click.secho("\n💡 INFO: Please populate the scaffolded files with specific API rules or version matching logic.", fg="cyan")
                        click.secho("  - Update skill.yaml to set tags and version range support (e.g. 'supported_versions: >=2.18.0').", fg="cyan")
                        click.secho("  - Add coding rules to rules.txt to direct model behavior.", fg="cyan")
                        click.secho("  - Add detailed code blocks to instructions.md and files in examples/.", fg="cyan")
                        click.secho("Refer to Part 2 Section 2-D of the User Guide for detailed instructions.", fg="cyan")
                    await kernel.stop()
                    sys.exit(0)

        await kernel.stop()
        
        click.secho("\n=== Generation Workflow Completed ===", bold=True)
        if res.get("toolchain_warning"):
            # Shown regardless of pass/fail - a version mismatch that didn't bite
            # THIS goal may still bite on a different machine or a later,
            # JVM-flag-sensitive one.
            click.secho(f"[TOOLCHAIN PREFLIGHT WARNING] {res['toolchain_warning']}", fg="yellow")
        if res.get("unresolved_skill_gaps"):
            # Shown regardless of pass/fail - a shaky success is exactly the case
            # this matters most for: nothing else would ever tell you the result
            # rests on a technology Kriya has no verified information for.
            click.secho(
                "[UNVERIFIED KNOWLEDGE] Generation proceeded without verified information for: "
                f"{', '.join(res['unresolved_skill_gaps'])}. Consider `kriya skills show <name>` "
                "or supplying reference material via a future run.",
                fg="yellow",
            )
        if res.get("files"):
            status_color = "green" if res.get('quality_gates_passed') else "red"
            status_text = "PASSED" if res.get('quality_gates_passed') else "FAILED"
            click.secho(f"Quality Gates: {status_text}", bold=True, fg=status_color)
            if res.get('quality_gates_passed'):
                click.echo(f"Files written to workspace: {', '.join(res['files'])}")
            else:
                click.secho(
                    f"Files attempted but NOT applied to workspace (quality gates failed): {', '.join(res['files'])}",
                    fg="red"
                )
                if res.get("failure_category"):
                    click.echo(f"Failure category: {res['failure_category']}")
                if res.get("environment_failure"):
                    click.secho(
                        f"\n[ENVIRONMENT/TOOLCHAIN ISSUE] {res['environment_failure']}\n"
                        "Kriya stopped retrying early rather than burning its retry budget "
                        "re-generating code that could never fix this - run `kriya doctor` "
                        "to check your Java/Maven toolchain resolution.",
                        fg="yellow", bold=True
                    )
                if res.get("run_id"):
                    click.secho(
                        f"Checkpoint saved - re-run with the same goal and add "
                        f"--resume-id {res['run_id']} (or just --resume) to retry Developer generation "
                        "without redoing Plan/Design.",
                        fg="yellow"
                    )
            if res.get("review"):
                click.secho("\n=== Reviewer Report & Run Instructions ===", bold=True, fg="cyan")
                click.echo(res.get("review"))
        else:
            click.secho("No files written (either rejected or empty changes).", fg="yellow")
        
    try:
        asyncio.run(run_workflow())
    except Exception as e:
        click.secho(f"Workflow error: {e}", fg="red")
        click.secho(
            "If a Plan/Design/Developer stage had already completed before this error, "
            "re-run the same command with --resume to pick up where it left off instead of starting over.",
            fg="yellow"
        )
        sys.exit(1)

@main.command()
@click.argument('file_path', type=click.Path(exists=True))
@click.pass_context
def review(ctx: click.Context, file_path: str) -> None:
    """Run code review agent on a file or a folder."""
    cfg: AppConfig = ctx.obj['config']

    llm = LLMClient(cfg)
    reviewer = ReviewerAgent("reviewer", llm, cfg.agent_llms.reviewer.llm, cfg.agent_llms.reviewer.llm_chain)

    REVIEW_EXTENSIONS = (".py", ".java", ".xml", ".rb")

    try:
        files_to_review = []
        truncated = False

        # Every click.secho below this point is progress/status narration, not
        # the review itself - kept on stderr (err=True) so only the reviewer's
        # actual streamed output lands on stdout, the same convention applied
        # to `prompt generate`/`analyze` for the identical reason: a real
        # shell pipe consuming this command's stdout (or a REPL-side "save
        # this review" redirect) shouldn't have to filter out narration mixed
        # into the same stream as the real content.
        if os.path.isdir(file_path):
            click.secho(f"Scanning directory: {file_path}", fg="cyan", err=True)
            # Try git status first. -z gives NUL-separated, unquoted paths - the
            # default porcelain format quotes paths containing spaces/special
            # characters (e.g. `M "path with spaces/file.py"`), which a naive
            # whitespace-split silently mangles.
            import subprocess
            res = subprocess.run(["git", "status", "--porcelain", "-z"], cwd=file_path, capture_output=True, text=True)
            if res.returncode == 0 and res.stdout.strip("\x00"):
                entries = res.stdout.split("\x00")
                i = 0
                while i < len(entries):
                    entry = entries[i]
                    if not entry:
                        i += 1
                        continue
                    # Normal entry: "XY PATH" (2-char status, space, path) - fixed
                    # positions, not a whitespace split, so paths containing spaces
                    # are handled correctly. A rename/copy entry is "XY NEW_PATH",
                    # i.e. entry[3:] is ALREADY the current/destination path (the
                    # one that exists on disk and is worth reviewing) - confirmed
                    # directly against real git output, not assumed: `git status
                    # --porcelain -z` puts the new path first and follows it with
                    # a SEPARATE bare (no status prefix) entry holding the OLD
                    # path, the opposite of what seems intuitive. That old-path
                    # continuation entry must be consumed (to keep the NUL-split
                    # stream aligned for whatever comes next) but never used as
                    # the file to review - using it looks up a path that no
                    # longer exists, silently finding nothing and falling through
                    # to the fallback recursive scan instead.
                    status, filepath = entry[:2], entry[3:]
                    if status[0] in ("R", "C") and i + 1 < len(entries):
                        i += 1  # consume and discard the old-path continuation entry
                    full = os.path.join(file_path, filepath)
                    if os.path.isfile(full) and filepath.endswith(REVIEW_EXTENSIONS):
                        files_to_review.append((filepath, full))
                    i += 1

            # Fallback to scanning folder recursively if no modified files found
            if not files_to_review:
                ignore_dirs = {".git", ".venv", "venv", "node_modules", "__pycache__"}
                for root, dirs, files in os.walk(file_path):
                    dirs[:] = [d for d in dirs if d not in ignore_dirs and not d.startswith(".")]
                    for file in files:
                        _, ext = os.path.splitext(file)
                        if ext.lower() in REVIEW_EXTENSIONS:
                            full = os.path.join(root, file)
                            rel = os.path.relpath(full, file_path)
                            files_to_review.append((rel, full))
                            if len(files_to_review) >= 10: # Cap at 10 files
                                truncated = True
                                break
                    if truncated:
                        break
                if truncated:
                    click.secho(
                        "Note: more than 10 reviewable files found - only the first 10 (by directory "
                        "scan order) are being reviewed. Re-run against a subdirectory for full coverage.",
                        fg="yellow", err=True,
                    )
        else:
            files_to_review.append((os.path.basename(file_path), os.path.abspath(file_path)))

        if not files_to_review:
            click.secho("No Python, Java, or XML source files found to review.", fg="yellow", err=True)
            return

        click.secho(f"Reviewing {len(files_to_review)} file(s)...", fg="cyan", err=True)
        from kriya.analyzer.analyzer import chunk_file_syntactically
        from kriya.workflow.workflow import estimate_tokens

        # Budget-aware batching. Confirmed live as a real, severe bug: with no
        # size control at all, a file (or file set) exceeding the model's context
        # window got silently truncated from the FRONT by the backend, cutting
        # off every "=== File: ... ===" framing marker along with it - the model
        # received an unlabeled fragment of raw code with no indication it was
        # even being asked to review anything, produced a confused non-review
        # response, and Kriya still reported success (exit 0) with no warning
        # at all. Same context_window * 0.75 budget convention used throughout
        # workflow.py, via the same estimate_tokens() heuristic - not a new one.
        budget = int(cfg.llm.context_window * 0.75)
        file_blobs = []  # (rel, blob_text, token_estimate)
        for rel, full in files_to_review:
            try:
                with open(full, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()

                chunks = chunk_file_syntactically(content, max_lines=150, overlap=15)
                blob = ""
                for c_idx, chunk_data in enumerate(chunks, 1):
                    suffix = f" (Part {c_idx})" if len(chunks) > 1 else ""
                    blob += f"\n=== File: {rel}{suffix} ===\n{chunk_data['text']}\n"

                if estimate_tokens(blob) > budget:
                    # Even this one file alone doesn't fit - keep as many whole
                    # chunks as fit and say so explicitly, both to the model (so
                    # it knows it's working from a partial view, not confidently
                    # reviewing what it thinks is the complete file) and to the
                    # user.
                    click.secho(
                        f"Warning: '{rel}' is too large to review in full within the "
                        f"configured context window - reviewing only the portion that fits.",
                        fg="yellow", err=True,
                    )
                    kept = ""
                    for c_idx, chunk_data in enumerate(chunks, 1):
                        suffix = f" (Part {c_idx})" if len(chunks) > 1 else ""
                        candidate = kept + f"\n=== File: {rel}{suffix} ===\n{chunk_data['text']}\n"
                        if estimate_tokens(candidate) > budget:
                            break
                        kept = candidate
                    blob = kept + f"\n=== File: {rel} - TRUNCATED: remainder omitted, file exceeds the review token budget ===\n"

                file_blobs.append((rel, blob, estimate_tokens(blob)))
            except Exception as e:
                click.secho(f"Failed to read file {rel}: {e}", fg="yellow", err=True)

        # Greedily group files into batches that each fit the budget - the
        # common case (a handful of small/medium files) still produces exactly
        # ONE batch, unchanged behavior: one combined call, full cross-file
        # architectural context for the reviewer. Only degrades to multiple
        # separate calls when the combined content genuinely wouldn't fit.
        batches: List[str] = []
        current_batch = ""
        current_tokens = 0
        for _rel, blob, tokens in file_blobs:
            if current_batch and current_tokens + tokens > budget:
                batches.append(current_batch)
                current_batch = ""
                current_tokens = 0
            current_batch += blob
            current_tokens += tokens
        if current_batch:
            batches.append(current_batch)

        import sys
        def on_stream(token: str):
            click.echo(token, nl=False)
            sys.stdout.flush()

        async def run_review():
            for i, batch_prompt in enumerate(batches, 1):
                label = "=== Code Review Report ===" if len(batches) == 1 else f"=== Code Review Report (batch {i}/{len(batches)}) ==="
                click.secho(f"\n{label}", bold=True, fg="cyan", err=True)
                await reviewer.run(batch_prompt, stream_callback=on_stream)
                click.echo()

        asyncio.run(run_review())
    except Exception as e:
        click.secho(f"Review failed: {e}", fg="red", err=True)
        sys.exit(1)

@main.command(name="ask")
@click.argument('question')
@click.pass_context
def ask(ctx: click.Context, question: str) -> None:
    """Ask Kriya questions about the codebase."""
    cfg: AppConfig = ctx.obj['config']
    
    from kriya.analyzer.analyzer import RepositoryAnalyzer
    analyzer = RepositoryAnalyzer(os.getcwd())
    repo_model = analyzer.analyze()
    repo_context = repo_model.model_dump_json(indent=2)
    
    # Extract key file contents (build manifests, main files, readmes) to provide local content context
    ENTRYPOINT_MARKERS = {
        ".java": "public static void main",
        ".py": "__main__",
        ".rb": "__FILE__",
    }
    KEY_MANIFEST_FILES = {
        "pom.xml", "beans.xml", "build.gradle", "README.md", "package.json",
        "requirements.txt", "pyproject.toml", "setup.py", "Gemfile", "Cargo.toml", "go.mod",
    }
    key_files_context = ""
    for root, dirs, files_list in os.walk(os.getcwd()):
        dirs[:] = [d for d in dirs if d not in {".git", ".venv", "venv", "node_modules", "target", "build", "__pycache__", ".pytest_cache", ".kriya"}]
        for file in files_list:
            _, ext = os.path.splitext(file)
            if file in KEY_MANIFEST_FILES or ext.lower() in {".java", ".py", ".rb", ".xml", ".json", ".yaml", ".yml"}:
                full_path = os.path.join(root, file)
                try:
                    if os.path.getsize(full_path) > 20480:
                        continue
                    rel_path = os.path.relpath(full_path, os.getcwd())
                    with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
                        file_content = fh.read()

                    # For java/py/ruby source files, prioritize main entry points or
                    # smaller scripts - skip large non-entry files to leave room for
                    # more relevant content. The entry-point marker is
                    # language-specific (Java's "public static void main" can never
                    # appear in a Python or Ruby file) - confirmed live as a real bug:
                    # using one marker for all three languages meant EVERY large
                    # Python/Ruby file got silently excluded regardless of whether it
                    # was the actual entry point, including Kriya's own cli.py.
                    marker = ENTRYPOINT_MARKERS.get(ext.lower())
                    if marker and marker not in file_content and len(file_content) > 3000:
                        continue

                    if len(key_files_context) < 30000:
                        key_files_context += f"\n=== File: {rel_path} ===\n{file_content}\n"
                except Exception as e:
                    logger.debug(f"Failed to read key file '{full_path}' for ask context: {e}")

    llm = LLMClient(cfg)
    
    system_prompt = (
        "You are Kriya, an expert codebase Q&A assistant.\n"
        "Your task is to answer the user's question about the repository based on the project layout, metadata, files context, and any web documentation context provided.\n"
        "If the user asks about a specific version of a framework/tool (e.g., Ignite 2.18.0) and you do not have local memory context or built-in specifics for it, state that you do not have its exact details and suggest they use 'kriya learn -u <url>' to teach you.\n"
        "Be concise, clear, and explain run/build scripts if requested."
    )
    
    import sys
    def on_stream(token: str):
        click.echo(token, nl=False)
        sys.stdout.flush()
        
    async def run_query():
        rag_context = ""
        index_path = os.path.join(cfg.paths.memory, "web_knowledge.db")
        if os.path.exists(index_path):
            try:
                from kriya.memory.vector import LocalVectorStore, OllamaEmbeddingClient
                embed_client = OllamaEmbeddingClient(
                    base_url=cfg.embedding.base_url,
                    model=cfg.embedding.model
                )
                vector_store = LocalVectorStore(index_path)
                query_emb = await embed_client.get_embedding(question, is_query=True)
                matches = vector_store.query(query_emb, top_k=5)
                good_matches = [m for m in matches if m["score"] > 0.4]
                if good_matches:
                    rag_context = "\n".join([f"[Source: {m['filepath']}]\n{m['text']}" for m in good_matches])
                vector_store.close()
            except Exception as e:
                logger.debug(f"Failed to query RAG database for ask command: {e}")

        user_prompt = (
            f"=== Repository Context ===\n{repo_context}\n\n"
            f"=== Key Files Context ===\n{key_files_context}\n\n"
            f"=== Web Resources Context ===\n{rag_context}\n\n"
            f"User Question: {question}"
        )
        return await llm.complete(system_prompt, user_prompt, stream_callback=on_stream)
        
    try:
        asyncio.run(run_query())
        click.echo()
    except Exception as e:
        click.secho(f"Failed to fetch answer: {e}", fg="red")
        sys.exit(1)

@main.command(name="learn")
@click.option('--url', '-u', multiple=True, help="URL to read and persist in local memory.")
@click.option('--file', '-f', multiple=True, type=click.Path(exists=True, dir_okay=False), help="Local plain-text file containing reference documentation.")
@click.option('--text', '-t', multiple=True, help="Raw text rules to index directly.")
@click.pass_context
def learn(ctx: click.Context, url: List[str], file: List[str], text: List[str]) -> None:
    """Index web pages, local files, or raw inline text into Kriya's local RAG memory."""
    if not url and not file and not text:
        click.secho("Error: Must provide at least one of --url (-u), --file (-f), or --text (-t).", fg="red")
        sys.exit(1)
        
    cfg: AppConfig = ctx.obj['config']
    
    from kriya.memory.vector import LocalVectorStore, OllamaEmbeddingClient
    from kriya.tools.web import fetch_url_text
    
    embed_client = OllamaEmbeddingClient(
        base_url=cfg.embedding.base_url,
        model=cfg.embedding.model
    )
    
    os.makedirs(cfg.paths.memory, exist_ok=True)
    index_path = os.path.join(cfg.paths.memory, "web_knowledge.db")
    vector_store = LocalVectorStore(index_path)
    
    async def index_text_content(source_name: str, content: str):
        chunks = []
        chunk_size = 1000
        overlap = 150
        start = 0
        while start < len(content):
            chunks.append(content[start:start+chunk_size])
            start += chunk_size - overlap
            
        click.echo(f"Generating embeddings for {len(chunks)} chunks of {source_name}...")
        embeddings = await embed_client.get_embeddings(chunks)

        # get_embeddings() silently substitutes an all-zero "dummy" vector on any
        # failure (embedding server unreachable, malformed response) to degrade
        # gracefully - reasonable for a caller like ask's RAG lookup, where a
        # dummy query vector is naturally filtered out by the similarity-score
        # threshold. But here that dummy vector gets WRITTEN permanently into the
        # index - confirmed live as a real bug: the content becomes silently
        # unsearchable forever (a zero vector never ranks meaningfully against a
        # real query) while still being reported as "Successfully indexed" with
        # no indication anything went wrong.
        failed_count = sum(1 for emb in embeddings if not any(v != 0.0 for v in emb))
        if failed_count:
            click.secho(
                f"Warning: embedding generation failed for {failed_count}/{len(chunks)} chunk(s) of "
                f"{source_name} (embedding server unreachable or returned an error). Those chunks "
                f"were still indexed but will NOT be findable via similarity search. Check your "
                f"embedding server connection and re-run this 'kriya learn' command to fix it.",
                fg="yellow",
            )

        # Clear existing learned chunks matching source_name
        vector_store.remove_learned_knowledge(source_name)

        import datetime
        fetch_date = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for chunk, emb in zip(chunks, embeddings, strict=True):
            vector_store.add_learned_knowledge(
                text=chunk,
                embedding=emb,
                model_name=cfg.embedding.model,
                dimensions=len(emb),
                provenance_url=source_name,
                fetch_date=fetch_date
            )
        click.secho(f"Successfully indexed: {source_name}", fg="green")
        
    async def process_sources():
        # 1. Process URL Web Sources
        #
        # Deliberately NOT gated by autonomy.egress_policy, unlike LLMClient.complete
        # (kriya/core/llm.py's is_local_url/EgressViolationError check) - considered
        # and explicitly decided against extending that check here. egress_policy's
        # local_only default guards against automatic, potentially-surprising network
        # calls a pipeline might make on its own (an LLM backend swap, generate's own
        # web_lookup_enabled-gated live lookup). A user typing a specific URL directly
        # into `learn -u` is itself the explicit, deliberate authorization - there's
        # no meaningfully more-consenting action Kriya could ask for. Blocking this
        # under local_only (Kriya's own default) would silently break `learn`'s own
        # documented, commonly-used URL-ingestion feature on every fresh install.
        for u in url:
            click.echo(f"Fetching and parsing web URL: {u}")
            try:
                content = await fetch_url_text(u)
                if not content:
                    click.secho(f"Empty content parsed from {u}", fg="yellow")
                    continue
                await index_text_content(u, content)
            except Exception as e:
                click.secho(f"Failed to index URL {u}: {e}", fg="red")

        # 2. Process Local File Sources
        for fp in file:
            click.echo(f"Reading local file: {fp}")
            try:
                with open(fp, "r", encoding="utf-8", errors="replace") as fh:
                    content = fh.read()
                if not content.strip():
                    click.secho(f"Empty content in file {fp}", fg="yellow")
                    continue
                await index_text_content(fp, content)
            except Exception as e:
                click.secho(f"Failed to index file {fp}: {e}", fg="red")

        # 3. Process Direct Text Rules
        #
        # source_name must be a stable identity, not a per-invocation positional
        # index ("Manual Entry {N}") - index_text_content() always calls
        # remove_learned_knowledge(source_name) first as an intentional
        # dedup-on-re-learn mechanism (re-fetching the same URL/file should
        # replace its old chunks). Confirmed live as a real data-loss bug: a
        # positional index collides across SEPARATE, unrelated invocations
        # (today's 1st --text is always "Manual Entry 1", tomorrow's 1st
        # --text is too, even though they're unrelated facts) - deleting an
        # earlier taught fact just because a later, different one happened to
        # land on the same index. A content hash gives distinct texts distinct
        # identities while still correctly deduping a genuine re-teach of the
        # exact same text.
        for txt in text:
            if not txt.strip():
                continue
            content_hash = hashlib.sha256(txt.encode("utf-8")).hexdigest()[:10]
            src_name = f"Manual Entry ({content_hash})"
            click.echo(f"Indexing inline text: {src_name}")
            try:
                await index_text_content(src_name, txt)
            except Exception as e:
                click.secho(f"Failed to index text entry: {e}", fg="red")

        # Persist index changes and close connection
        vector_store.save()
        vector_store.close()
        
    try:
        asyncio.run(process_sources())
        click.secho("Local knowledge base updated successfully.", bold=True, fg="green")
    except Exception as e:
        click.secho(f"Learning process failed: {e}", fg="red")
        sys.exit(1)

@main.command(name="fix")
@click.option('--error', '-e', help="Compilation or test error log string. If omitted, reads from stdin.")
@click.option('--workspace', '-w', default=".", type=click.Path(exists=True, file_okay=False), help="Workspace directory path.")
@click.option('--yes', '-y', is_flag=True, help="Auto-approve patch application without prompting.")
@click.option('--resume', is_flag=True, default=False, help="Resume the most recently saved checkpoint for this workspace.")
@click.option('--resume-id', default=None, help="Resume a specific checkpoint by run_id instead of the latest one.")
@click.pass_context
def fix(ctx: click.Context, error: Optional[str], workspace: str, yes: bool, resume: bool, resume_id: Optional[str]) -> None:
    """Diagnose and automatically apply patches for compiler or test errors."""
    piped_stdin = not sys.stdin.isatty()
    if not error:
        if piped_stdin:
            error = sys.stdin.read()
        else:
            click.secho("Error: Must provide --error (-e) or pipe error log to stdin.", fg="red")
            sys.exit(1)
            
    if piped_stdin and not yes:
        click.secho("Error: Non-TTY (piped) input detected. You must specify the '--yes' (-y) flag to auto-approve patch application.", fg="red")
        sys.exit(1)
            
    cfg: AppConfig = ctx.obj['config']
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    we = WorkflowEngine(kernel, llm)
    
    click.echo(f"Initiating diagnostic repair workflow on: {os.path.abspath(workspace)}")
    
    async def run_fix():
        def step_cb(step_name, content):
            click.secho(f"\n[{step_name.upper()}]", bold=True, fg="cyan")
            click.echo(content[:300] + "..." if len(content) > 300 else content)
            
        def approval_cb(diffs, reason):
            click.secho(f"\n[HUMAN REVIEW GATE REQUIRED]: {reason}", bold=True, fg="yellow")
            for diff in diffs:
                click.secho(f"File: {diff['filepath']}", bold=True)
                click.echo(diff['content'])
            if yes:
                click.secho("Auto-approving patch application (-y / --yes specified).", fg="green")
                return True
            try:
                with open("/dev/tty", "r") as tty:
                    sys.stdout.write("Apply these changes to your active workspace? [Y/n]: ")
                    sys.stdout.flush()
                    val = tty.readline().strip().lower()
                    return not val or val.startswith("y")
            except Exception:
                return click.confirm("Apply these changes to your active workspace?", default=True)

        res = await we.run_generation_workflow(
            goal="Fix compilation/test failure",
            workspace_path=workspace,
            step_callback=step_cb,
            approval_callback=approval_cb,
            error_context=error,
            resume=resume,
            resume_id=resume_id
        )
        if res.get("toolchain_warning"):
            click.secho(f"\n[TOOLCHAIN PREFLIGHT WARNING] {res['toolchain_warning']}", fg="yellow")
        if res.get("unresolved_skill_gaps"):
            click.secho(
                "\n[UNVERIFIED KNOWLEDGE] Generation proceeded without verified information for: "
                f"{', '.join(res['unresolved_skill_gaps'])}.",
                fg="yellow",
            )
        if res["quality_gates_passed"]:
            click.secho("\n[SUCCESS] Diagnostic repair completed successfully! Compiled and verified.", fg="green", bold=True)
        else:
            click.secho("\n[FAILURE] Repair attempts completed but compilation/tests still fail.", fg="red", bold=True)
            if res.get("failure_category"):
                click.echo(f"Failure category: {res['failure_category']}")
            if res.get("environment_failure"):
                click.secho(
                    f"\n[ENVIRONMENT/TOOLCHAIN ISSUE] {res['environment_failure']}\n"
                    "Kriya stopped retrying early rather than burning its retry budget "
                    "re-generating code that could never fix this - run `kriya doctor` "
                    "to check your Java/Maven toolchain resolution.",
                    fg="yellow", bold=True
                )
            if res.get("run_id"):
                click.secho(
                    f"Checkpoint saved - re-run with the same --error and add "
                    f"--resume-id {res['run_id']} (or just --resume) to retry Developer generation "
                    "without redoing Plan/Design.",
                    fg="yellow"
                )

    try:
        asyncio.run(run_fix())
    except Exception as e:
        click.secho(f"Error executing fix workflow: {e}", fg="red")
        click.secho(
            "If a Plan/Design/Developer stage had already completed before this error, "
            "re-run the same command with --resume to pick up where it left off instead of starting over.",
            fg="yellow"
        )
        sys.exit(1)

@main.command(name="traces")
@click.option("-n", "--limit", type=int, default=20, show_default=True, help="Maximum number of most-recent runs to show. Use --all to show every run.")
@click.option("--all", "show_all", is_flag=True, help="Show all recorded runs, ignoring --limit.")
@click.pass_context
def traces(ctx: click.Context, limit: int, show_all: bool) -> None:
    """Show persistent run trace logs and metrics of past runs."""
    cfg: AppConfig = ctx.obj['config']
    db_path = os.path.join(cfg.paths.logs, "traces.db")
    if not os.path.exists(db_path):
        click.echo("No run traces recorded yet.")
        return

    from kriya.core.db import get_connection
    conn = get_connection(db_path)
    cursor = conn.cursor()
    total = cursor.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    query = "SELECT run_id, timestamp, goal, duration_sec, attempts, status, files_modified FROM runs ORDER BY timestamp DESC"
    if not show_all:
        query += " LIMIT ?"
        cursor.execute(query, (limit,))
    else:
        cursor.execute(query)
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        click.echo("No run traces recorded yet.")
        return

    click.secho(f"{'TIMESTAMP':<20} | {'STATUS':<10} | {'ATTEMPTS':<8} | {'DURATION':<10} | {'GOAL':<40}", bold=True)
    click.echo("-" * 100)
    for _r_id, ts, goal, dur, att, status, _files in rows:
        dur_str = f"{dur:.2f}s"
        status_color = "green" if status.lower() == "success" else "red"
        status_styled = click.style(f"{status:<10}", fg=status_color)
        click.echo(f"{ts:<20} | {status_styled} | {att:<8} | {dur_str:<10} | {goal[:40]:<40}")

    if not show_all and total > len(rows):
        click.echo(f"\nShowing {len(rows)} of {total} recorded runs. Use -n/--limit or --all to see more.")

if __name__ == '__main__':
    main()
