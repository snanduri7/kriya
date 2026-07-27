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
        try:
            skill = se.get_skill(f"auto-{repo_slug}")
            if skill.rules or skill.instructions:
                convention_prompt = "\n\n=== Project Coding Conventions & Rules ===\n"
                if skill.rules:
                    convention_prompt += "Rules:\n" + "\n".join(f"- {r}" for r in skill.rules) + "\n"
                if skill.instructions:
                    convention_prompt += f"Instructions:\n{skill.instructions}\n"
                logger.info(f"Loaded dynamic workspace skill conventions for repository '{repo_slug}'.")
        except KeyError:
            pass
        
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
        max_retries = 3
        retry_count = 0
        error_context = ""
        files_written = []

        while retry_count < max_retries:
            try:
                task_desc = f"Goal: {goal}\nPlan: {plan}"
                if error_context:
                    task_desc += f"\n\n=== Previous Error to Fix ===\n{error_context}"

                # Generate code files
                dev_stream = (lambda token: stream_callback("Code Generation", token)) if stream_callback else None
                files = await self.developer.run_generation(
                    task_description=task_desc,
                    design_context=design,
                    existing_code_context=convention_prompt,
                    stream_callback=dev_stream
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
