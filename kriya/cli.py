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
@click.option('--changed', '-d', is_flag=True, default=False, help="Only index files changed in git.")
@click.option('--force', '-f', is_flag=True, default=False, help="Force complete re-indexing.")
@click.pass_context
def analyze(ctx: click.Context, path: str, changed: bool, force: bool) -> None:
    """Analyze and index a repository directory."""
    cfg: AppConfig = ctx.obj['config']
    analyzer = RepositoryAnalyzer(path)
    try:
        model = analyzer.analyze()
        click.echo(model.model_dump_json(indent=2))
        
        if os.path.isdir(path):
            click.secho("\nBuilding semantic repository index...", bold=True, fg="cyan")
            
            progress_bar = None
            
            def progress_callback(filepath: str, idx: int, total: int):
                nonlocal progress_bar
                if progress_bar is None:
                    progress_bar = click.progressbar(length=total, label="Indexing repository files")
                
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
        click.echo(f"  - {s.name}: {s.description} [Category: {s.category}]")
        staged_file = os.path.join(cfg.paths.skills, s.name.lower(), "staged_rules.txt")
        if not os.path.exists(staged_file):
            staged_file = os.path.join(cfg.paths.skills, s.name, "staged_rules.txt")
        if os.path.exists(staged_file):
            try:
                with open(staged_file, "r", encoding="utf-8") as sf:
                    lines = [l.strip() for l in sf if l.strip()]
                if lines:
                    click.secho(f"    [STAGED RULES PENDING REVIEW ({len(lines)})]:", fg="yellow")
                    for l in lines:
                        click.echo(f"      * {l}")
            except Exception:
                pass

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
            staged_lines = [l.strip() for l in sf if l.strip()]
            
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

@main.command()
@click.argument('goal')
@click.option('--yes', '-y', is_flag=True, default=False, help="Auto-approve all proposed code changes.")
@click.pass_context
def generate(ctx: click.Context, goal: str, yes: bool) -> None:
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

    async def run_workflow():
        nonlocal goal
        rag_context = ""
        index_path = os.path.join(cfg.paths.memory, "web_knowledge.db")
        if os.path.exists(index_path):
            try:
                from kriya.memory.vector import OllamaEmbeddingClient, LocalVectorStore
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
    
    # Extract key file contents (pom.xml, beans.xml, main files, readmes) to provide local content context
    key_files_context = ""
    for root, dirs, files_list in os.walk(os.getcwd()):
        dirs[:] = [d for d in dirs if d not in {".git", ".venv", "venv", "node_modules", "target", "build", "__pycache__", ".pytest_cache"}]
        for file in files_list:
            _, ext = os.path.splitext(file)
            if file in {"pom.xml", "beans.xml", "build.gradle", "README.md", "package.json"} or ext.lower() in {".java", ".py", ".rb", ".xml", ".json", ".yaml", ".yml"}:
                full_path = os.path.join(root, file)
                try:
                    if os.path.getsize(full_path) > 20480:
                        continue
                    rel_path = os.path.relpath(full_path, os.getcwd())
                    with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
                        file_content = fh.read()
                    
                    # For java/py/ruby source files, prioritize main entry points or smaller scripts
                    if ext.lower() in {".java", ".py", ".rb"} and "public static void main" not in file_content and len(file_content) > 3000:
                        continue
                        
                    if len(key_files_context) < 30000:
                        key_files_context += f"\n=== File: {rel_path} ===\n{file_content}\n"
                except Exception:
                    pass

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
                from kriya.memory.vector import OllamaEmbeddingClient, LocalVectorStore
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
                pass
                
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
    
    from kriya.tools.web import fetch_url_text
    from kriya.memory.vector import OllamaEmbeddingClient, LocalVectorStore
    
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
        
        # Clear existing learned chunks matching source_name
        vector_store.remove_learned_knowledge(source_name)
        
        import datetime
        fetch_date = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for chunk, emb in zip(chunks, embeddings):
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
        for t_idx, txt in enumerate(text, 1):
            if not txt.strip():
                continue
            src_name = f"Manual Entry {t_idx}"
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
@click.pass_context
def fix(ctx: click.Context, error: Optional[str], workspace: str, yes: bool) -> None:
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
            error_context=error
        )
        if res["quality_gates_passed"]:
            click.secho("\n[SUCCESS] Diagnostic repair completed successfully! Compiled and verified.", fg="green", bold=True)
        else:
            click.secho("\n[FAILURE] Repair attempts completed but compilation/tests still fail.", fg="red", bold=True)
            
    try:
        asyncio.run(run_fix())
    except Exception as e:
        click.secho(f"Error executing fix workflow: {e}", fg="red")
        sys.exit(1)

@main.command(name="traces")
@click.pass_context
def traces(ctx: click.Context) -> None:
    """Show persistent run trace logs and metrics of past runs."""
    cfg: AppConfig = ctx.obj['config']
    db_path = os.path.join(cfg.paths.logs, "traces.db")
    if not os.path.exists(db_path):
        click.echo("No run traces recorded yet.")
        return
        
    import sqlite3
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT run_id, timestamp, goal, duration_sec, attempts, status, files_modified FROM runs ORDER BY timestamp DESC")
    rows = cursor.fetchall()
    conn.close()
    
    if not rows:
        click.echo("No run traces recorded yet.")
        return
        
    click.secho(f"{'TIMESTAMP':<20} | {'STATUS':<10} | {'ATTEMPTS':<8} | {'DURATION':<10} | {'GOAL':<40}", bold=True)
    click.echo("-" * 100)
    for r_id, ts, goal, dur, att, status, files in rows:
        dur_str = f"{dur:.2f}s"
        status_color = "green" if status.lower() == "success" else "red"
        status_styled = click.style(f"{status:<10}", fg=status_color)
        click.echo(f"{ts:<20} | {status_styled} | {att:<8} | {dur_str:<10} | {goal[:40]:<40}")

if __name__ == '__main__':
    main()
