import os
import re
import sys
import logging
import asyncio
from typing import Dict, Any, List, Callable, Optional

from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.analyzer.analyzer import RepositoryAnalyzer
from kriya.agents.agent import PlannerAgent, ArchitectAgent, DeveloperAgent, ReviewerAgent

logger = logging.getLogger(__name__)

class WorkflowEngine:
    """Orchestrates multi-agent pipelines and auto-debugging loops (Quality Gates)."""

    def __init__(self, kernel: Kernel, llm_client: LLMClient) -> None:
        self.kernel = kernel
        self.llm = llm_client
        self.planner = PlannerAgent("planner", llm_client)
        self.architect = ArchitectAgent("architect", llm_client)
        self.developer = DeveloperAgent("developer", llm_client)
        self.reviewer = ReviewerAgent("reviewer", llm_client)

    async def run_generation_workflow(
        self, 
        goal: str, 
        workspace_path: str,
        step_callback: Optional[Callable[[str, str], None]] = None,
        approval_callback: Optional[Callable[[List[Dict[str, str]], str], Any]] = None,
        stream_callback: Optional[Callable[[str, str], None]] = None
    ) -> Dict[str, Any]:
        """Runs the complete Planner -> Architect -> Developer -> Quality Gates -> Reviewer loop (supporting streaming)."""
        
        # 1. Analyze repository context
        logger.info("Analyzing workspace context...")
        analyzer = RepositoryAnalyzer(workspace_path)
        repo_model = analyzer.analyze()
        repo_context = repo_model.model_dump_json(indent=2)
        
        # Load local workspace conventions if present
        from kriya.skills.skill import SkillEngine
        repo_slug = os.path.basename(workspace_path).lower().strip(".")
        if not repo_slug:
            repo_slug = "root"
            
        skills_dir = self.kernel.config.paths.skills
        se = SkillEngine(skills_dir)
        se.discover_and_load()
        
        convention_prompt = ""
        for skill in se.list_skills():
            # Match skill if its name or tags are mentioned in the goal (case-insensitive),
            # or if it's the auto-generated convention for this repository.
            is_relevant = (
                skill.name.lower() in goal.lower() or
                any(tag.lower() in goal.lower() for tag in skill.tags) or
                skill.name.lower() == f"auto-{repo_slug}"
            )
            if is_relevant:
                if skill.rules or skill.instructions:
                    convention_prompt += f"\n\n=== Engineering Skill Conventions: {skill.name} ===\n"
                    if skill.rules:
                        convention_prompt += "Rules:\n" + "\n".join(f"- {r}" for r in skill.rules) + "\n"
                    if skill.instructions:
                        convention_prompt += f"Instructions:\n{skill.instructions}\n"
                    logger.info(f"Loaded engineering skill '{skill.name}' for generation context.")
        
        # 1.5. Graph RAG Context Retrieval
        graph_rag_context = ""
        try:
            vector_index_path = os.path.join(self.kernel.config.paths.memory, "vector_index.json")
            db_path = os.path.join(self.kernel.config.paths.memory, "dependency_graph.db")
            
            if os.path.exists(vector_index_path):
                from kriya.memory.vector import OllamaEmbeddingClient, LocalVectorStore
                embed_client = OllamaEmbeddingClient(
                    base_url=self.kernel.config.embedding.base_url,
                    model=self.kernel.config.embedding.model
                )
                vector_store = LocalVectorStore(vector_index_path)
                
                query_emb = await embed_client.get_embedding(goal)
                matches = vector_store.query(query_emb, top_k=5)
                good_matches = [m for m in matches if m["score"] > 0.35]
                
                if good_matches:
                    graph_rag_context += "\n\n=== Codebase Semantic Reference Context ===\n"
                    matched_files = list(dict.fromkeys([m["filepath"] for m in good_matches]))
                    for file in matched_files:
                        full_p = os.path.join(workspace_path, file)
                        if os.path.exists(full_p):
                            try:
                                with open(full_p, "r", encoding="utf-8") as fh:
                                    content = fh.read()
                                if len(content) > 2000:
                                    content = content[:2000] + "\n... (truncated)"
                                graph_rag_context += f"\nFile: {file}\n{content}\n"
                            except Exception:
                                pass
                                
                    if os.path.exists(db_path):
                        from kriya.analyzer.graph import DependencyGraph
                        graph = DependencyGraph(db_path)
                        
                        related_files = set()
                        for file in matched_files:
                            sym_name = os.path.splitext(os.path.basename(file))[0]
                            
                            for caller in graph.get_callers(sym_name):
                                if caller.get("filepath"):
                                    related_files.add(caller["filepath"])
                                    
                            for callee in graph.get_callees(sym_name):
                                if callee.get("filepath"):
                                    related_files.add(callee["filepath"])
                                    
                            for imp in graph.get_imports(file):
                                if imp.startswith(workspace_path) or os.path.exists(os.path.join(workspace_path, imp)):
                                    related_files.add(os.path.relpath(imp, workspace_path))
                                    
                        related_files = [f for f in related_files if f not in matched_files]
                        if related_files:
                            graph_rag_context += "\n=== Related AST Graph Files (Callers/Callees) ===\n"
                            for f in related_files[:3]:
                                full_p = os.path.join(workspace_path, f)
                                if os.path.exists(full_p):
                                    try:
                                        with open(full_p, "r", encoding="utf-8") as fh:
                                            content = fh.read()
                                        if len(content) > 1500:
                                            content = content[:1500] + "\n... (truncated)"
                                        graph_rag_context += f"\nFile: {f}\n{content}\n"
                                    except Exception:
                                        pass
        except Exception as ex:
            logger.warning(f"Failed to query Graph RAG: {ex}")
            
        if graph_rag_context:
            convention_prompt += graph_rag_context
            
        # 2. Plan
        logger.info("Planner Agent drafting execution steps...")
        plan_stream = (lambda token: stream_callback("Planning", token)) if stream_callback else None
        plan_prompt = f"Goal: {goal}\n\nWorkspace Context:\n{repo_context}" + convention_prompt
        plan = await self.planner.run(
            plan_prompt, 
            stream_callback=plan_stream
        )
        if step_callback:
            step_callback("Plan", plan)

        # 3. Architect
        logger.info("Architect Agent defining interface designs...")
        architect_stream = (lambda token: stream_callback("Architect Design", token)) if stream_callback else None
        design_prompt = f"Plan:\n{plan}\n\nWorkspace Context:\n{repo_context}" + convention_prompt
        design = await self.architect.run(
            design_prompt, 
            stream_callback=architect_stream
        )
        if step_callback:
            step_callback("Design", design)

        # 4. Developer & Quality Gates (Auto-debugging loop)
        logger.info("Developer Agent implementing source files...")
        chain = self.kernel.config.llm_chain
        max_retries = 1 + len(chain) if chain else 3
        retry_count = 0
        error_context = ""
        files_written = []

        while retry_count < max_retries:
            try:
                task_desc = f"Goal: {goal}\nPlan: {plan}"
                if error_context:
                    task_desc += f"\n\n=== Previous Error to Fix ===\n{error_context}"

                # Configure model overrides if escalating in the chain
                model_override = None
                base_url_override = None
                api_key_override = None
                if retry_count > 0 and chain:
                    fallback_idx = min(retry_count - 1, len(chain) - 1)
                    fallback = chain[fallback_idx]
                    model_override = fallback.model
                    base_url_override = fallback.base_url
                    api_key_override = fallback.api_key
                    logger.info(f"Escalating compilation attempt to fallback model: {model_override}")

                # Generate code files
                dev_stream = (lambda token: stream_callback("Code Generation", token)) if stream_callback else None
                files = await self.developer.run_generation(
                    task_description=task_desc,
                    design_context=design,
                    existing_code_context=convention_prompt,
                    stream_callback=dev_stream,
                    model_override=model_override,
                    base_url_override=base_url_override,
                    api_key_override=api_key_override
                )
                
                # Sensitive paths & risk threshold validation checks
                autonomy_cfg = self.kernel.config.autonomy
                need_approval = autonomy_cfg.mode == "human-in-the-loop"
                reason = "Human-in-the-loop review policy"
                
                sensitive_match = False
                for file_obj in files:
                    filepath = file_obj.get("filepath", "")
                    for pattern in autonomy_cfg.sensitive_paths:
                        try:
                            if re.match(pattern, filepath, re.IGNORECASE):
                                sensitive_match = True
                                reason = f"Sensitive path matched: {filepath} ({pattern})"
                                break
                        except Exception:
                            pass
                    if sensitive_match:
                        break
                        
                if sensitive_match:
                    need_approval = True
                    
                total_lines = sum(len(f.get("content", "").splitlines()) for f in files)
                if total_lines > autonomy_cfg.risk_threshold_lines:
                    need_approval = True
                    reason = f"Risk threshold exceeded ({total_lines} lines > {autonomy_cfg.risk_threshold_lines})"
                    
                if need_approval and approval_callback:
                    logger.info(f"Escalating to human review: {reason}")
                    # Await callback resolution
                    approved = approval_callback(files, reason)
                    if asyncio.iscoroutine(approved):
                        approved = await approved
                    if not approved:
                        logger.info("Human rejected changes. Aborting workflow.")
                        return {
                            "plan": plan,
                            "design": design,
                            "files": [],
                            "quality_gates_passed": False,
                            "review": "Rejected by user during diff review."
                        }
                
                # Write files to disk
                files_written = []
                for file_obj in files:
                    filepath = file_obj.get("filepath", "")
                    content = file_obj.get("content", "")
                    if not filepath or not content:
                        continue
                    
                    full_path = os.path.join(workspace_path, filepath)
                    os.makedirs(os.path.dirname(full_path), exist_ok=True)
                    with open(full_path, "w", encoding="utf-8") as f:
                        f.write(content)
                    files_written.append(filepath)
                    logger.info(f"Wrote generated file to: {filepath}")

                # Quality Gates: Polymorphic compile & test checks
                logger.info("Quality Gates: Running polymorphic compiler and test checks...")
                from kriya.tools.validate import PolymorphicValidator
                validator = PolymorphicValidator(workspace_path)
                
                compile_res = validator.run_compile_check(files_written)
                if not compile_res["success"]:
                    raise ValueError(f"COMPILATION FAILURE:\n{compile_res['output']}")
                    
                test_written = any("test" in f.lower() or "spec" in f.lower() for f in files_written)
                if test_written:
                    logger.info(f"Quality Gates: Executing tests for {validator.stack} stack...")
                    test_res = validator.run_tests()
                    if not test_res["success"]:
                        raise ValueError(f"TEST FAILURE:\n{test_res['output']}")
                
                # If we made it here, Quality Gates passed successfully!
                logger.info("Quality Gates check PASSED.")
                
                # Autonomous Skill Accrual / Lesson extraction
                if retry_count > 0 and chain:
                    try:
                        logger.info("Escalation model successfully resolved the compilation/test issue! Extracting lessons learned...")
                        extract_prompt = (
                            f"A compilation/test error occurred:\n{error_context}\n\n"
                            f"The files were successfully fixed with this final content:\n"
                        )
                        for filepath in files_written:
                            full_path = os.path.join(workspace_path, filepath)
                            try:
                                with open(full_path, "r", encoding="utf-8") as fh:
                                    extract_prompt += f"=== File: {filepath} ===\n{fh.read()}\n"
                            except Exception:
                                pass
                        extract_prompt += "\nExtract a single, concise coding rule (maximum 1 sentence) explaining the fix so that future models can avoid the same error. Do not output anything else, just the sentence starting with a capital letter."
                        
                        lesson = await self.llm.complete(
                            system_prompt="You are a senior compiler architect. Extract the core rule/lesson from this compile/test error resolution.",
                            user_prompt=extract_prompt,
                            model_override=model_override,
                            base_url_override=base_url_override,
                            api_key_override=api_key_override
                        )
                        lesson = lesson.strip().strip('"').strip("'")
                        if lesson:
                            logger.info(f"Extracted lesson: {lesson}")
                            skills_dir = self.kernel.config.paths.skills
                            skill_folder = os.path.join(skills_dir, f"auto-{repo_slug}")
                            os.makedirs(skill_folder, exist_ok=True)
                            rules_file = os.path.join(skill_folder, "rules.txt")
                            
                            existing_rules = []
                            if os.path.exists(rules_file):
                                with open(rules_file, "r", encoding="utf-8") as rf:
                                    existing_rules = [line.strip() for line in rf if line.strip()]
                            
                            if lesson not in existing_rules and not any(lesson.lower() in r.lower() for r in existing_rules):
                                with open(rules_file, "a", encoding="utf-8") as rf:
                                    rf.write(f"\n{lesson}")
                                logger.info(f"Appended extracted lesson rule to {rules_file}")
                    except Exception as ex:
                        logger.warning(f"Failed to extract lesson or update skills: {ex}")

                break

            except Exception as e:
                retry_count += 1
                logger.warning(f"Quality Gates FAILED (Attempt {retry_count}/{max_retries}): {e}")
                error_context = str(e)
                if retry_count >= max_retries:
                    logger.error("Quality Gates exceeded maximum debug retries. Continuing to review with errors.")
                    
        # 5. Reviewer
        logger.info("Reviewer Agent evaluating results...")
        review_prompt = f"Goal: {goal}\n\nFiles generated:\n"
        for filepath in files_written:
            full_path = os.path.join(workspace_path, filepath)
            try:
                with open(full_path, "r", encoding="utf-8") as f:
                    content = f.read()
                review_prompt += f"\n=== File: {filepath} ===\n{content}\n"
            except Exception:
                pass
                
        reviewer_stream = (lambda token: stream_callback("Review", token)) if stream_callback else None
        review = await self.reviewer.run(review_prompt, stream_callback=reviewer_stream)
        if step_callback:
            step_callback("Review", review)

        return {
            "plan": plan,
            "design": design,
            "files": files_written,
            "quality_gates_passed": retry_count < max_retries,
            "review": review
        }
