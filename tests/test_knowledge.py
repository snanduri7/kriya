import os
import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from kriya.tools.knowledge import (
    KnowledgeGuard,
    RegistryAdapterFactory,
    extract_library_versions,
    parse_iso_datetime,
)


def test_extract_library_versions():
    goal = "Build a Spring Boot 3.2.0 app with org.apache.ignite:ignite-core:2.18.0 and express@4.18.2 plus requests==2.31.0"
    libs = extract_library_versions(goal)
    libs_dict = dict(libs)
    
    assert "org.apache.ignite:ignite-core" in libs_dict
    assert libs_dict["org.apache.ignite:ignite-core"] == "2.18.0"
    
    assert "org.springframework.boot:spring-boot-starter" in libs_dict
    assert libs_dict["org.springframework.boot:spring-boot-starter"] == "3.2.0"
    
    assert "express" in libs_dict
    assert libs_dict["express"] == "4.18.2"
    
    assert "requests" in libs_dict
    assert libs_dict["requests"] == "2.31.0"

def test_parse_iso_datetime():
    assert parse_iso_datetime("2024-03-15T00:00:00Z") == datetime(2024, 3, 15, 0, 0, tzinfo=timezone.utc)
    assert parse_iso_datetime("2024-03-15T00:00:00+00:00") == datetime(2024, 3, 15, 0, 0, tzinfo=timezone.utc)
    assert parse_iso_datetime("") is None
    assert parse_iso_datetime("invalid-date") is None

def test_detect_stack(tmp_path):
    assert RegistryAdapterFactory.detect_stack(str(tmp_path)) == "unknown"
    
    (tmp_path / "pom.xml").write_text("<project></project>")
    assert RegistryAdapterFactory.detect_stack(str(tmp_path)) == "java"
    (tmp_path / "pom.xml").unlink()
    
    (tmp_path / "package.json").write_text("{}")
    assert RegistryAdapterFactory.detect_stack(str(tmp_path)) == "javascript"
    (tmp_path / "package.json").unlink()

    (tmp_path / "requirements.txt").write_text("")
    assert RegistryAdapterFactory.detect_stack(str(tmp_path)) == "python"

def test_maven_central_adapter_success():
    adapter = RegistryAdapterFactory.from_stack("java")
    
    # Mock successful SOLR response
    with patch("httpx.Client") as MockClient:
        mock_client = MockClient.return_value.__enter__.return_value
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "response": {
                "docs": [{"timestamp": 1710460800000}]  # 2024-03-15
            }
        }
        mock_client.get.return_value = mock_resp
        
        rel_date = adapter.get_release_date("org.apache.ignite:ignite-core", "2.18.0")
        assert rel_date is not None
        assert rel_date.year == 2024
        assert rel_date.month == 3
        assert rel_date.day == 15

def test_pypi_adapter_success():
    adapter = RegistryAdapterFactory.from_stack("python")
    
    with patch("httpx.Client") as MockClient:
        mock_client = MockClient.return_value.__enter__.return_value
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "urls": [{"upload_time": "2024-03-15T12:00:00Z"}]
        }
        mock_client.get.return_value = mock_resp
        
        rel_date = adapter.get_release_date("requests", "2.31.0")
        assert rel_date == datetime(2024, 3, 15, 12, 0, tzinfo=timezone.utc)

def test_npm_adapter_success():
    adapter = RegistryAdapterFactory.from_stack("javascript")
    
    with patch("httpx.Client") as MockClient:
        mock_client = MockClient.return_value.__enter__.return_value
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "time": {"4.18.2": "2024-03-15T12:00:00Z"}
        }
        mock_client.get.return_value = mock_resp
        
        rel_date = adapter.get_release_date("express", "4.18.2")
        assert rel_date == datetime(2024, 3, 15, 12, 0, tzinfo=timezone.utc)

def test_knowledge_guard_gaps(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    
    # 1. Structural Gap Check: skills folder is empty
    kg = KnowledgeGuard(str(skills_dir), "2023-12-01")
    
    with patch("httpx.Client") as MockClient:
        mock_client = MockClient.return_value.__enter__.return_value
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "response": {
                "docs": [{"timestamp": 1710460800000}]  # 2024-03-15 (after cutoff)
            }
        }
        mock_client.get.return_value = mock_resp
        
        report = kg.check_goal("Build spring-boot 3.2.0")
        assert report.has_gaps
        # Should be HIGH risk because release is post-cutoff AND skill file is missing
        assert report.gaps[0]["risk_level"] == "HIGH"

def test_knowledge_guard_no_gaps(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    
    # Pre-populate skill to remove structural gap
    (skills_dir / "spring-boot-3.2.0").mkdir()
    (skills_dir / "spring-boot-3.2.0" / "skill.yaml").write_text("name: spring-boot")
    
    kg = KnowledgeGuard(str(skills_dir), "2025-01-01")  # Cutoff is after release
    
    with patch("httpx.Client") as MockClient:
        mock_client = MockClient.return_value.__enter__.return_value
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "response": {
                "docs": [{"timestamp": 1710460800000}]  # 2024-03-15 (before cutoff)
            }
        }
        mock_client.get.return_value = mock_resp
        
        report = kg.check_goal("Build spring-boot 3.2.0")
        assert not report.has_gaps

def test_generate_skill_template(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    
    kg = KnowledgeGuard(str(skills_dir), "2023-12-01")
    t_dir = kg.generate_skill_template("org.apache.ignite:ignite-core", "2.18.0")
    
    assert os.path.exists(t_dir)
    assert os.path.exists(os.path.join(t_dir, "skill.yaml"))
    assert os.path.exists(os.path.join(t_dir, "rules.txt"))
    assert os.path.exists(os.path.join(t_dir, "instructions.md"))
    assert os.path.exists(os.path.join(t_dir, "examples", "Example.java"))
    
    # Verify contents
    with open(os.path.join(t_dir, "skill.yaml"), "r") as f:
        content = f.read()
        assert "org.apache.ignite-ignite-core" in content
        assert "2.18.0" in content

def test_knowledge_cache(tmp_path):
    from kriya.tools.knowledge import KnowledgeCache
    cache = KnowledgeCache(str(tmp_path))
    
    # 1. Cache hit should return None initially
    assert cache.get_release_date("java", "org.apache.ignite:ignite-core", "2.18.0") is None
    
    # 2. Write to cache
    dt = datetime(2024, 3, 15, 12, 0, tzinfo=timezone.utc)
    cache.set_release_date("java", "org.apache.ignite:ignite-core", "2.18.0", dt)
    
    # 3. Cache read should return the datetime
    cached_dt = cache.get_release_date("java", "org.apache.ignite:ignite-core", "2.18.0")
    assert cached_dt == dt


def test_knowledge_cache_expires_stale_entries_and_supports_invalidation(tmp_path):
    from kriya.tools.knowledge import KnowledgeCache
    cache = KnowledgeCache(str(tmp_path), ttl_days=1)
    dt = datetime(2024, 3, 15, 12, 0, tzinfo=timezone.utc)
    cache.set_release_date("java", "example:library", "1.0", dt, "RegistryFixture")
    with sqlite3.connect(cache.db_path) as conn:
        conn.execute(
            "UPDATE release_cache SET retrieved_at = ?",
            ((datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),),
        )
    assert cache.get_release_date("java", "example:library", "1.0") is None
    assert cache.last_lookup_metadata["cache_status"] == "expired"
    cache.set_release_date("java", "example:library", "1.0", dt, "RegistryFixture")
    assert cache.last_lookup_metadata["source"] == "RegistryFixture"
    cache.invalidate("java", "example:library", "1.0")
    assert cache.get_release_date("java", "example:library", "1.0") is None


def test_knowledge_cache_uses_default_ttl_for_non_numeric_legacy_config(tmp_path):
    from kriya.tools.knowledge import KnowledgeCache
    cache = KnowledgeCache(str(tmp_path), ttl_days=MagicMock())
    assert cache.ttl == timedelta(days=30)


def test_knowledge_guard_with_cache(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    
    kg = KnowledgeGuard(str(skills_dir), "2023-12-01", memory_dir=str(memory_dir))
    
    # Pre-populate cache to avoid HTTP request
    dt = datetime(2024, 3, 15, 12, 0, tzinfo=timezone.utc)
    kg.cache.set_release_date("java", "org.apache.ignite:ignite-core", "2.18.0", dt)
    
    # Call check_goal - should hit cache
    with patch("httpx.Client") as MockClient:
        # MockClient should NOT be called because of cache hit
        report = kg.check_goal("Build org.apache.ignite:ignite-core:2.18.0")
        assert report.has_gaps
        assert len(report.gaps) == 1
        assert report.gaps[0]["library"] == "org.apache.ignite:ignite-core"
        assert report.gaps[0]["risk_level"] == "HIGH"
        MockClient.assert_not_called()

def test_workflow_stage_2a_gap(tmp_path):
    from kriya.config.config import AppConfig
    from kriya.core.kernel import Kernel
    from kriya.core.llm import LLMClient
    from kriya.workflow.workflow import WorkflowEngine
    
    cfg = AppConfig()
    cfg.paths.skills = str(tmp_path / "skills")
    cfg.paths.memory = str(tmp_path / "memory")
    os.makedirs(cfg.paths.skills)
    os.makedirs(cfg.paths.memory)
    
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    we = WorkflowEngine(kernel, llm)
    
    # Mock planner and architect to return a design that includes a gap
    from unittest.mock import AsyncMock
    we.planner.run = AsyncMock(return_value="Valid Plan")
    we.architect.run = AsyncMock(return_value="Design proposes org.apache.ignite:ignite-core:2.18.0")
    
    # We prime the cache with a post-cutoff release date
    from kriya.tools.knowledge import KnowledgeCache
    cache = KnowledgeCache(cfg.paths.memory)
    cache.set_release_date("java", "org.apache.ignite:ignite-core", "2.18.0", datetime(2024, 3, 15, 0, 0, tzinfo=timezone.utc))
    
    # mock approval callback to reject
    mock_approval = MagicMock(return_value=False)
    
    with patch("kriya.workflow.workflow.RepositoryAnalyzer") as MockAnalyzer:
        mock_analyzer = MockAnalyzer.return_value
        mock_analyzer.analyze.return_value.dependencies = []
        mock_analyzer.analyze.return_value.frameworks = []
        mock_analyzer.analyze.return_value.model_dump_json.return_value = "{}"
        
        with pytest.raises(ValueError, match="User rejected post-cutoff dependency risk in Stage 2A"):
            import asyncio
            asyncio.run(we.run_generation_workflow(
                goal="Build a standard application",
                workspace_path=str(tmp_path),
                approval_callback=mock_approval,
                knowledge_risk_confirmed=True  # Confirm stage 0, trigger stage 2A check
            ))
            
        # Verify approval callback was indeed queried for the Stage 2A gap
        assert mock_approval.called

def test_workflow_auto_accrual(tmp_path):
    from unittest.mock import AsyncMock

    from kriya.config.config import AppConfig
    from kriya.core.kernel import Kernel
    from kriya.core.llm import LLMClient
    from kriya.workflow.workflow import WorkflowEngine

    cfg = AppConfig()
    cfg.paths.skills = str(tmp_path / "skills")
    cfg.paths.memory = str(tmp_path / "memory")
    os.makedirs(cfg.paths.skills)
    os.makedirs(cfg.paths.memory)

    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    we = WorkflowEngine(kernel, llm)

    # 1. We mock the workflow execution so that:
    # - Attempt 1 fails compiling with a resolver suggestion
    # - Attempt 2 compiles successfully
    we.planner.run = AsyncMock(return_value="Drafting plan")
    we.architect.run = AsyncMock(return_value="Designing structure")
    # Content must actually include the suggested artifact - auto-accrual now
    # requires real evidence the suggestion was used, not just that it was
    # offered at some point during a retry (see the dedicated "not accrued
    # when unused" test below for the case this guards against).
    we.developer.run_generation = AsyncMock(return_value=[{"filepath": "pom.xml", "content": "<project><artifactId>artemis-server</artifactId></project>"}])
    we.reviewer.run = AsyncMock(return_value="Passed Review")
    we.llm.complete = AsyncMock(return_value="Valid Lesson")

    # We mock PolymorphicValidator to fail on attempt 1 with a dependency suggestion, and succeed on attempt 2
    mock_run_compile = MagicMock()
    mock_run_compile.side_effect = [
        {
            "success": False,
            "output": "Maven compilation failed:\n=== KRIYA PLATFORM DEPENDENCY SUGGESTIONS ===\n[KRIYA SUGGESTION] Missing item was matched to Maven dependency:\n<dependency>\n    <groupId>org.apache.activemq</groupId>\n    <artifactId>artemis-server</artifactId>\n    <version>2.31.2</version>\n</dependency>\n=============================================\n"
        },
        {"success": True, "output": "Maven compilation succeeded."}
    ]

    # Patch the methods on the class, not the class itself: PolymorphicValidator is
    # constructed at TWO separate sites for a successful run - attempt.py's per-attempt
    # compile/test gate (a deferred, function-local import that re-resolves
    # kriya.tools.validate.PolymorphicValidator at call time) and workflow.py's own
    # final full-regression-suite check (imported once at module load, so it holds its
    # own frozen reference to the real class - patching the module attribute after
    # that import already happened never reaches it). Replacing the whole class via
    # patch("kriya.tools.validate.PolymorphicValidator") only affects the first call
    # site; the second silently falls through to a REAL PolymorphicValidator against
    # this test's minimal mocked pom.xml content, producing genuine Maven validation
    # errors ('modelVersion' is missing, etc.) instead of using the mock. Patching the
    # methods directly on the class object affects every instance regardless of which
    # module's reference constructed it - the same pattern already used everywhere
    # else in tests/test_workflow.py (see e.g. test_workflow_checks_toolchain_only_once_across_retries's
    # own "regardless of which of the two PolymorphicValidator construction sites
    # reaches it first" comment).
    with patch("kriya.workflow.workflow.RepositoryAnalyzer") as MockAnalyzer, \
         patch("kriya.tools.validate.PolymorphicValidator.run_compile_check", new=mock_run_compile), \
         patch("kriya.tools.validate.PolymorphicValidator.run_pom_validate", return_value={"success": True, "output": ""}), \
         patch("kriya.tools.validate.PolymorphicValidator.run_tests", return_value={"success": True, "output": "Tests passed"}):

        mock_analyzer = MockAnalyzer.return_value
        mock_analyzer.analyze.return_value.dependencies = []
        mock_analyzer.analyze.return_value.frameworks = []
        mock_analyzer.analyze.return_value.model_dump_json.return_value = "{}"

        # Disable human approval to auto-apply
        cfg.autonomy.mode = "autonomous"
        cfg.autonomy.risk_threshold_lines = 1000

        import asyncio
        res = asyncio.run(we.run_generation_workflow(
            goal="Build a standard application",
            workspace_path=str(tmp_path),
            knowledge_risk_confirmed=True
        ))

        assert res["quality_gates_passed"] is True

        # Verify that the custom skill directory was automatically accrued in the skills folder!
        expected_skill_path = tmp_path / "skills" / "org.apache.activemq-artemis-server-2.31.2"
        assert os.path.exists(expected_skill_path)
        assert os.path.exists(expected_skill_path / "skill.yaml")


def test_workflow_auto_accrual_skipped_when_suggestion_never_actually_used(tmp_path):
    """Regression test for a real bug found via a live golden-use-case run
    (M1, Ignite): the resolver.py Maven-Central-search dependency-suggestion
    feature matched a completely unrelated library (cc.mashroom:mashroom-
    plugin) for a generic missing-symbol name during a transient compile
    failure whose real cause was a wrong import path, not a missing
    dependency - the model never actually added that dependency anywhere,
    but auto-accrual still permanently scaffolded a skill for it (git-
    committed), which then polluted a completely unrelated LATER goal's
    skill-gap detection ("unverified skill(s) relevant to this goal:
    cc.mashroom-mashroom-plugin"). A suggestion merely appearing in a
    compile-error's enrichment block during some retry is not evidence it
    was ever used - only the coordinate genuinely appearing in the final
    applied file content should trigger accrual."""
    from unittest.mock import AsyncMock

    from kriya.config.config import AppConfig
    from kriya.core.kernel import Kernel
    from kriya.core.llm import LLMClient
    from kriya.workflow.workflow import WorkflowEngine

    cfg = AppConfig()
    cfg.paths.skills = str(tmp_path / "skills")
    cfg.paths.memory = str(tmp_path / "memory")
    os.makedirs(cfg.paths.skills)
    os.makedirs(cfg.paths.memory)

    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    we = WorkflowEngine(kernel, llm)

    we.planner.run = AsyncMock(return_value="Drafting plan")
    we.architect.run = AsyncMock(return_value="Designing structure")
    # The suggested artifact ("mashroom-plugin") never actually appears here -
    # the real fix that made attempt 2 pass was unrelated to the suggestion.
    we.developer.run_generation = AsyncMock(return_value=[{"filepath": "pom.xml", "content": "<project></project>"}])
    we.reviewer.run = AsyncMock(return_value="Passed Review")
    we.llm.complete = AsyncMock(return_value="Valid Lesson")

    mock_run_compile = MagicMock()
    mock_run_compile.side_effect = [
        {
            "success": False,
            "output": "Maven compilation failed:\n=== KRIYA PLATFORM DEPENDENCY SUGGESTIONS ===\n[KRIYA SUGGESTION] Missing item was matched to Maven dependency:\n<dependency>\n    <groupId>cc.mashroom</groupId>\n    <artifactId>mashroom-plugin</artifactId>\n    <version>None</version>\n</dependency>\n=============================================\n"
        },
        {"success": True, "output": "Maven compilation succeeded."}
    ]

    # See test_workflow_auto_accrual's comment above for why methods are patched on
    # the class rather than replacing the whole class - workflow.py's own final
    # regression-check PolymorphicValidator construction site otherwise falls through
    # to real Maven validation unmocked.
    with patch("kriya.workflow.workflow.RepositoryAnalyzer") as MockAnalyzer, \
         patch("kriya.tools.validate.PolymorphicValidator.run_compile_check", new=mock_run_compile), \
         patch("kriya.tools.validate.PolymorphicValidator.run_pom_validate", return_value={"success": True, "output": ""}), \
         patch("kriya.tools.validate.PolymorphicValidator.run_tests", return_value={"success": True, "output": "Tests passed"}):

        mock_analyzer = MockAnalyzer.return_value
        mock_analyzer.analyze.return_value.dependencies = []
        mock_analyzer.analyze.return_value.frameworks = []
        mock_analyzer.analyze.return_value.model_dump_json.return_value = "{}"

        cfg.autonomy.mode = "autonomous"
        cfg.autonomy.risk_threshold_lines = 1000

        import asyncio
        res = asyncio.run(we.run_generation_workflow(
            goal="Build a standard application",
            workspace_path=str(tmp_path),
            knowledge_risk_confirmed=True
        ))

        assert res["quality_gates_passed"] is True

        # Must NOT have been accrued - the suggestion was never actually used.
        unexpected_skill_path = tmp_path / "skills" / "cc.mashroom-mashroom-plugin-None"
        assert not os.path.exists(unexpected_skill_path)


def test_knowledge_guard_bare_mention_gap_without_skill(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()

    kg = KnowledgeGuard(str(skills_dir), "2023-12-01", offline=True)
    report = kg.check_goal("Build a broker using Redhat qpid MRG in server mode")

    assert report.has_gaps
    gap = next(g for g in report.gaps if g["library"] == "org.apache.qpid:qpid-broker-core")
    assert gap["version"] == "unspecified"
    assert gap["release_date"] is None
    assert gap["risk_level"] == "MEDIUM"

def test_knowledge_guard_bare_mention_no_gap_with_skill(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "qpid").mkdir()
    (skills_dir / "qpid" / "skill.yaml").write_text("name: qpid\ntags: [qpid]")

    kg = KnowledgeGuard(str(skills_dir), "2023-12-01", offline=True)
    report = kg.check_goal("Build a broker using Redhat qpid MRG in server mode")

    assert not report.has_gaps

def test_extract_library_versions_bare_mention_skipped_when_version_present():
    goal = "Apache Ignite 2.18.0 running embedded, with Redhat qpid MRG as the broker and Apache Ignite 2.18.0 for cache"
    libs = extract_library_versions(goal)
    libs_dict = dict(libs)

    assert libs_dict["org.apache.ignite:ignite-core"] == "2.18.0"
    assert libs_dict["org.apache.qpid:qpid-broker-core"] == "unspecified"
    # Ignite must not also appear as a spurious second "unspecified" entry
    assert len([lib for lib in libs if lib[0] == "org.apache.ignite:ignite-core"]) == 1

def test_generate_skill_template_without_version(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()

    kg = KnowledgeGuard(str(skills_dir), "2023-12-01")
    t_dir = kg.generate_skill_template("org.apache.qpid:qpid-broker-core", "unspecified")

    assert os.path.basename(t_dir) == "org.apache.qpid-qpid-broker-core"
    with open(os.path.join(t_dir, "skill.yaml"), "r") as f:
        content = f.read()
        assert 'supported_versions: "*"' in content
        assert "unspecified" not in content

def test_extract_library_versions_deduplication():
    goal = "Apache Ignite 2.18 and org.apache.ignite:ignite-core:2.18.0 and apache-ignite 2.18"
    libs = extract_library_versions(goal)
    # Both "Apache Ignite 2.18" (which canonicalizes to org.apache.ignite:ignite-core:2.18.0) and "org.apache.ignite:ignite-core:2.18.0" should be normalized and deduplicated into a single entry!
    assert len(libs) == 1
    assert libs[0] == ("org.apache.ignite:ignite-core", "2.18.0")
