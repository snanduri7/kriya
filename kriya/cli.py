import os
import sys
import urllib.request
import urllib.error
import json
import asyncio
from typing import Optional
import click

from kriya import __version__
from kriya.config import load_config, AppConfig
from kriya.core.kernel import Kernel
from kriya.plugins.plugin import PluginManager
from kriya.prompt import PromptEngine
from kriya.analyzer import RepositoryAnalyzer
from kriya.skills import SkillEngine
from kriya.core import LLMClient
from kriya.workflow import WorkflowEngine
from kriya.agents import ReviewerAgent

@click.group()
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

@main.command()
def version() -> None:
    """Print the Kriya platform version."""
    click.echo(f"Kriya version: {__version__}")

@main.command()
@click.pass_context
def config(ctx: click.Context) -> None:
    """Display current Kriya configuration."""
    cfg: AppConfig = ctx.obj['config']
    click.echo(cfg.model_dump_json(indent=2))

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
        
    # 2. Check local LLM connection
    click.echo("\nChecking LLM provider connectivity:")
    provider = cfg.llm.provider
    base_url = cfg.llm.base_url
    model = cfg.llm.model
    click.echo(f"  - Provider: {provider}")
    click.echo(f"  - Base URL: {base_url}")
    click.echo(f"  - Model: {model}")
    
    if provider == "openai":
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
                
                click.secho(f"  - [SUCCESS] Connected to local LLM server. Status code: 200", fg="green")
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
        except Exception as e:
            click.secho(f"  - [ERROR] Connectivity test encountered an error: {e}", fg="red")

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
            
        click.secho(f"=== Loaded Plugins ({len(loaded)}) ===", bold=True)
        for p in loaded:
            click.echo(f"  - {p.name} (v{p.version})")
    except Exception as e:
        click.secho(f"Error loading plugins: {e}", fg="red")

@main.group(name="prompt")
def prompt_group() -> None:
    """Manage and render prompt templates."""
    pass

@prompt_group.command(name="render")
@click.argument('template_name')
@click.option('--var', '-v', multiple=True, help="Variables to pass to template in key=value format.")
def prompt_render(template_name: str, var: tuple) -> None:
    """Render a prompt template with variables."""
    vars_dict = {}
    for variable in var:
        if '=' in variable:
            k, v = variable.split('=', 1)
            vars_dict[k.strip()] = v.strip()
        else:
            click.secho(f"Invalid variable format '{variable}'. Expected 'key=value'.", fg="red")
            sys.exit(1)
            
    pe = PromptEngine()
    try:
        rendered = pe.render(template_name, vars_dict)
        click.echo(rendered)
    except Exception as e:
        click.secho(f"Error: {e}", fg="red")
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
            await pm.initialize_all()
            
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
                
            await pm.shutdown_all()
            await kernel.stop()
            
        asyncio.run(run_list())
    except Exception as e:
        click.secho(f"Error: {e}", fg="red")

@tools_group.command(name="execute")
@click.argument('tool_name')
@click.argument('arguments_json', required=False)
@click.pass_context
def tools_execute(ctx: click.Context, tool_name: str, arguments_json: Optional[str]) -> None:
    """Execute a tool with JSON arguments."""
    cfg: AppConfig = ctx.parent.obj['config'] if ctx.parent else load_config()
    
    kernel = Kernel(config=cfg)
    pm = PluginManager(kernel=kernel, plugin_dir=cfg.plugins.directory)
    
    try:
        pm.discover_and_load(enabled_plugins=cfg.plugins.enabled)
        
        async def run_exec():
            await kernel.start()
            await pm.initialize_all()
            
            try:
                tool = kernel.registry.get("tool", tool_name)
            except Exception:
                click.secho(f"Tool '{tool_name}' not found.", fg="red")
                await pm.shutdown_all()
                await kernel.stop()
                sys.exit(1)
                
            args = json.loads(arguments_json) if arguments_json else {}
            
            try:
                result = await tool.execute(**args)
                if isinstance(result, (dict, list)):
                    click.echo(json.dumps(result, indent=2))
                else:
                    click.echo(result)
            except Exception as ex:
                click.secho(f"Execution failed: {ex}", fg="red")
                
            await pm.shutdown_all()
            await kernel.stop()
            
        asyncio.run(run_exec())
    except Exception as e:
        click.secho(f"Execution failed: {e}", fg="red")
        sys.exit(1)

@main.command()
@click.argument('path', type=click.Path(exists=True))
@click.pass_context
def analyze(ctx: click.Context, path: str) -> None:
    """Analyze and index a repository directory."""
    cfg: AppConfig = ctx.obj['config']
    analyzer = RepositoryAnalyzer(path)
    try:
        model = analyzer.analyze()
        click.echo(model.model_dump_json(indent=2))
        
        if os.path.isdir(path):
            click.secho("\nBuilding semantic repository index...", bold=True, fg="cyan")
            
            def progress_callback(filepath: str, idx: int, total: int):
                click.echo(f"[{idx}/{total}] Indexing: {filepath}")
                
            async def run_indexing():
                await analyzer.index_repository(cfg, progress_callback=progress_callback)
                
            asyncio.run(run_indexing())
            click.secho("Success: Semantic index compiled and cached to disk.", fg="green")
    except Exception as e:
        click.secho(f"Analysis failed: {e}", fg="red")
        sys.exit(1)

@main.group(name="skills")
def skills_group() -> None:
    """Manage platform engineering skills."""
    pass

@skills_group.command(name="list")
@click.pass_context
def skills_list(ctx: click.Context) -> None:
    """List all registered skills."""
    cfg: AppConfig = ctx.parent.obj['config'] if ctx.parent else load_config()
    se = SkillEngine(cfg.paths.skills)
    se.discover_and_load()
    
    skills = se.list_skills()
    if not skills:
        click.echo("No skills discovered. You can create one with 'kriya skills create <name>'.")
        return
        
    click.secho(f"=== Discovered Skills ({len(skills)}) ===", bold=True)
    for s in skills:
        click.echo(f"  - {s.name}: {s.description} [Category: {s.category}]")

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
        
        if s.rules:
            click.secho("\nRules:", bold=True)
            for r in s.rules:
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
    
    os.makedirs(cfg.paths.skills, exist_ok=True)
    
    se = SkillEngine(cfg.paths.skills)
    try:
        path = se.create_skill_skeleton(skill_name)
        click.secho(f"Successfully created skill skeleton at: {path}", fg="green")
    except Exception as e:
        click.secho(f"Failed to create skill: {e}", fg="red")
        sys.exit(1)

@main.command()
@click.argument('goal')
@click.pass_context
def generate(ctx: click.Context, goal: str) -> None:
    """Run autonomous multi-agent pipeline to satisfy a goal."""
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

    async def run_workflow():
        await kernel.start()
        res = await we.run_generation_workflow(
            goal=goal, 
            workspace_path=os.getcwd(), 
            approval_callback=on_approval,
            stream_callback=on_stream
        )
        await kernel.stop()
        
        click.secho("\n=== Generation Workflow Completed ===", bold=True)
        if res.get("files"):
            click.echo(f"Files written: {', '.join(res['files'])}")
            status_color = "green" if res.get('quality_gates_passed') else "red"
            status_text = "PASSED" if res.get('quality_gates_passed') else "FAILED"
            click.secho(f"Quality Gates: {status_text}", bold=True, fg=status_color)
        else:
            click.secho("No files written (either rejected or empty changes).", fg="yellow")
        
    try:
        asyncio.run(run_workflow())
    except Exception as e:
        click.secho(f"Workflow error: {e}", fg="red")
        sys.exit(1)

@main.command()
@click.argument('file_path', type=click.Path(exists=True))
@click.pass_context
def review(ctx: click.Context, file_path: str) -> None:
    """Run code review agent on a file or a folder."""
    cfg: AppConfig = ctx.obj['config']
    
    llm = LLMClient(cfg)
    reviewer = ReviewerAgent("reviewer", llm)
    
    try:
        files_to_review = []
        
        if os.path.isdir(file_path):
            click.secho(f"Scanning directory: {file_path}", fg="cyan")
            # Try git status first
            import subprocess
            res = subprocess.run(["git", "status", "--porcelain"], cwd=file_path, capture_output=True, text=True)
            if res.returncode == 0 and res.stdout.strip():
                for line in res.stdout.splitlines():
                    parts = line.strip().split()
                    if len(parts) >= 2:
                        filepath = parts[-1]
                        full = os.path.join(file_path, filepath)
                        if os.path.isfile(full) and filepath.endswith((".py", ".java", ".xml")):
                            files_to_review.append((filepath, full))
            
            # Fallback to scanning folder recursively if no modified files found
            if not files_to_review:
                ignore_dirs = {".git", ".venv", "venv", "node_modules", "__pycache__"}
                for root, dirs, files in os.walk(file_path):
                    dirs[:] = [d for d in dirs if d not in ignore_dirs and not d.startswith(".")]
                    for file in files:
                        _, ext = os.path.splitext(file)
                        if ext.lower() in {".py", ".java", ".xml"}:
                            full = os.path.join(root, file)
                            rel = os.path.relpath(full, file_path)
                            files_to_review.append((rel, full))
                            if len(files_to_review) >= 10: # Cap at 10 files
                                break
                    if len(files_to_review) >= 10:
                        break
        else:
            files_to_review.append((os.path.basename(file_path), os.path.abspath(file_path)))

        if not files_to_review:
            click.secho("No Python, Java, or XML source files found to review.", fg="yellow")
            return

        click.secho(f"Reviewing {len(files_to_review)} file(s)...", fg="cyan")
        review_prompt = ""
        from kriya.analyzer.analyzer import chunk_file_syntactically
        
        for rel, full in files_to_review:
            try:
                with open(full, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                
                chunks = chunk_file_syntactically(content, max_lines=150, overlap=15)
                for c_idx, chunk_data in enumerate(chunks, 1):
                    suffix = f" (Part {c_idx})" if len(chunks) > 1 else ""
                    review_prompt += f"\n=== File: {rel}{suffix} ===\n{chunk_data['text']}\n"
            except Exception as e:
                click.secho(f"Failed to read file {rel}: {e}", fg="yellow")

        import sys
        def on_stream(token: str):
            click.echo(token, nl=False)
            sys.stdout.flush()

        async def run_review():
            click.secho(f"\n=== Code Review Report ===", bold=True, fg="cyan")
            return await reviewer.run(review_prompt, stream_callback=on_stream)
            
        res = asyncio.run(run_review())
        click.echo()
    except Exception as e:
        click.secho(f"Review failed: {e}", fg="red")
        sys.exit(1)

if __name__ == '__main__':
    main()
