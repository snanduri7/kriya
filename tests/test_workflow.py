import hashlib
import json
import logging
import os
import sqlite3
import subprocess
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kriya.agents.agent import DeveloperAgent
from kriya.config import AppConfig, LLMConfig
from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.workflow.attempt import AttemptContext, run_attempt
from kriya.workflow.failure import Failure, QualityGateFailure
from kriya.workflow.retry_strategy import handle_attempt_failure
from kriya.workflow.state import GenerationState
from kriya.workflow.checkpoint import (
    checkpoint_path,
    compute_config_fingerprint,
    compute_workspace_fingerprint,
    find_latest_checkpoint,
    load_checkpoint,
    save_checkpoint,
)
from kriya.workflow.workflow import (
    RESOURCE_LIFECYCLE_HEADER,
    IncompleteGenerationError,
    WorkflowEngine,
    _augment_error_with_live_lookup,
    _build_ecosystem_invariant_block,
    _build_error_source_context,
    _build_full_set_retry_prompt,
    _build_lsp_diagnostics_context,
    _build_missing_files_retry_prompt,
    _build_targeted_retry_prompt,
    _check_java_toolchain_mismatch,
    _detect_missing_build_manifest,
    _filter_misattributed_extraction,
    _get_or_start_jdtls_client,
    _goal_or_repo_targets_java,
    _is_near_duplicate_rule,
    _java_toolchain_fact,
    _likely_misattributed_sibling,
    _normalize_error_for_repeat_detection,
    _reserve_graph_context_budget,
    _reserve_sibling_content_budget,
    build_code_context,
    _resolve_file_paths_from_design,
    _pin_exec_plugin_executable_to_resolved_jdk,
    _resolve_java_home_override,
    _resolve_jdk_home_for_version,
    _resolve_maven_main_class,
    _resolve_run_command,
    downgrade_ungrounded_goal_explicit_commands,
    check_plan_completeness,
    extract_planner_code_blocks,
    _scoped_skill_gap_description,
    _strip_jdk_incompatible_jvm_flags,
    classify_environment_failure,
    estimate_tokens,
    extract_contract_verdict,
    extract_error_search_terms,
    pass_verdict_is_grounded,
    extract_error_source_locations,
    extract_expected_files,
    extract_implicated_files,
    find_edits_ignoring_own_diagnosis,
    find_edits_ignoring_reported_line,
    find_misdirected_edit_target,
    find_missing_expected_files,
    find_structural_corruption,
    find_whole_response_no_op,
    atomic_write_file,
    normalize_written_filepath,
    _resolve_file_locations,
)


def _init_git_repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("placeholder\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=tmp_path, check=True)


def _seed_checkpoint(tmp_path, cfg, goal, run_id, stage, **extra):
    save_checkpoint(str(tmp_path), run_id, {
        "stage": stage,
        "workspace_fingerprint": compute_workspace_fingerprint(str(tmp_path)),
        "config_fingerprint": compute_config_fingerprint(cfg.model_dump()),
        "goal_fingerprint": hashlib.sha256(f"{goal}\x00".encode("utf-8")).hexdigest(),
        **extra,
    })


def test_extract_expected_files_from_design_tree():
    design = """
    src/main/java/com/example/
              |-- App.java
              |-- BrokerServer.java
          |-- ignite-config.xml

    Maven Dependencies (pom.xml)
    """
    expected = extract_expected_files(design)
    assert expected == {"App.java", "BrokerServer.java", "ignite-config.xml", "pom.xml"}

def test_extract_expected_files_empty_for_no_design():
    assert extract_expected_files("") == set()
    assert extract_expected_files("Just a plain description, no filenames here.") == set()

def test_find_missing_expected_files_matches_by_basename():
    expected = {"App.java", "BrokerServer.java", "pom.xml"}
    written = {"pom.xml", "src/main/java/com/example/App.java"}
    assert find_missing_expected_files(expected, written) == ["BrokerServer.java"]

def test_find_missing_expected_files_none_missing():
    expected = {"pom.xml"}
    written = {"pom.xml"}
    assert find_missing_expected_files(expected, written) == []

def test_find_missing_expected_files_excludes_unrequested_test_file():
    expected = {"App.java", "MessageServiceTest.java", "pom.xml"}
    written = {"App.java", "pom.xml"}
    goal = "Build a messaging app that sends and reads messages."
    assert find_missing_expected_files(expected, written, goal=goal) == []

def test_find_missing_expected_files_keeps_test_file_when_requested():
    expected = {"App.java", "MessageServiceTest.java", "pom.xml"}
    written = {"App.java", "pom.xml"}
    goal = "Build a messaging app with unit test coverage for the message service."
    assert find_missing_expected_files(expected, written, goal=goal) == ["MessageServiceTest.java"]

def test_find_missing_expected_files_excludes_readme_unless_requested():
    expected = {"App.java", "README.md"}
    written = {"App.java"}
    assert find_missing_expected_files(expected, written, goal="Build an app") == []
    assert find_missing_expected_files(expected, written, goal="Build an app with documentation") == ["README.md"]

def test_resolve_file_paths_from_design_finds_nested_path_in_design_text():
    """Regression test for a real bug caught live: find_missing_expected_files only
    ever returns bare basenames, since the Architect's design text isn't parsed for
    directory structure - but the design text itself usually DOES mention the real
    path (e.g. a bullet list line), so it can be recovered without depending on the
    model's own (confirmed unreliable) file-list response."""
    design = (
        "Files to create:\n"
        "- pom.xml\n"
        "- src/main/resources/ignite-qpid-app-context.xml\n"
        "- src/main/java/com/example/IgniteQpidApp.java\n"
    )
    assert _resolve_file_paths_from_design(
        ["ignite-qpid-app-context.xml", "IgniteQpidApp.java", "pom.xml"], design
    ) == [
        "src/main/resources/ignite-qpid-app-context.xml",
        "src/main/java/com/example/IgniteQpidApp.java",
        "pom.xml",
    ]

def test_resolve_file_paths_from_design_ignores_tree_diagram_lines():
    # A directory-tree diagram line has no real "/" immediately before the
    # basename (just tree-drawing characters), so it must not match - falling
    # back to the bare basename is correct when no bullet/prose mention exists.
    design = "│       ├── foo.xml\n"
    assert _resolve_file_paths_from_design(["foo.xml"], design) == ["foo.xml"]

def test_resolve_file_paths_from_design_falls_back_when_no_path_mentioned():
    assert _resolve_file_paths_from_design(["pom.xml"], "Create pom.xml for the project.") == ["pom.xml"]

@pytest.mark.asyncio
async def test_workflow_uses_structured_architect_file_list_for_known_target_files(tmp_path):
    """Demonstrates the actual value-add of the Architect file-list contract
    (kriya/agents/contracts.py) over the old heuristic it supersedes: a
    design whose prose NEVER mentions the nested directory a file actually
    belongs in - only the trailing JSON file list does.
    _resolve_file_paths_from_design's regex heuristic has nothing to match
    here and would have fallen back to the bare basename ("Person.java"),
    silently writing it flat at the sandbox root instead of its real nested
    location - the exact class of bug this contract exists to close. The
    structured path gets it right because it never needs to re-derive a
    path from prose at all."""
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        (
            "This design wires a Person record into an Ignite cache. No "
            "directory structure is described anywhere in this prose.\n\n"
            "```json\n"
            '{"files": ["src/main/java/com/example/Person.java", "pom.xml"]}\n'
            "```\n"
        ),
        "Review: Approved",
    ])
    we = WorkflowEngine(kernel, llm)
    we.developer.run_generation = AsyncMock(return_value=[
        {"filepath": "src/main/java/com/example/Person.java", "content": "public class Person {}"},
        {"filepath": "pom.xml", "content": "<project></project>"},
    ])
    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check", return_value={"success": True, "output": ""}), \
         patch("kriya.tools.validate.PolymorphicValidator.run_pom_validate", return_value={"success": True, "output": ""}), \
         patch("kriya.tools.validate.PolymorphicValidator.run_tests", return_value={"success": True, "output": ""}):
        res = await we.run_generation_workflow(goal="Create a Java app", workspace_path=str(tmp_path))

    assert res["quality_gates_passed"] is True
    first_call_kwargs = we.developer.run_generation.call_args_list[0].kwargs
    assert first_call_kwargs["known_target_files"] == [
        "pom.xml", "src/main/java/com/example/Person.java",
    ]

@pytest.mark.asyncio
async def test_workflow_falls_back_to_heuristic_file_list_when_architect_response_has_no_json(tmp_path):
    """Regression/safety-net test: an Architect response with no valid JSON
    file-list block (an old-style plain-prose design, or a model that just
    ignores the new instruction) must not fail the run - it degrades to the
    older heuristic extraction (extract_expected_files/
    _resolve_file_paths_from_design), reproducing the exact behavior Kriya
    had before the structured contract existed, with no extra completion
    call spent trying to recover it."""
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: create pom.xml and Main.java, no JSON list here.",
        "Review: Approved",
    ])
    we = WorkflowEngine(kernel, llm)
    we.developer.run_generation = AsyncMock(return_value=[
        {"filepath": "pom.xml", "content": "<project></project>"},
        {"filepath": "Main.java", "content": "public class Main {}"},
    ])
    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check", return_value={"success": True, "output": ""}), \
         patch("kriya.tools.validate.PolymorphicValidator.run_pom_validate", return_value={"success": True, "output": ""}), \
         patch("kriya.tools.validate.PolymorphicValidator.run_tests", return_value={"success": True, "output": ""}):
        res = await we.run_generation_workflow(goal="Create a Java app", workspace_path=str(tmp_path))

    assert res["quality_gates_passed"] is True
    first_call_kwargs = we.developer.run_generation.call_args_list[0].kwargs
    assert first_call_kwargs["known_target_files"] == ["Main.java", "pom.xml"]
    # Exactly 3 completions (Planner, Architect, Reviewer) - no extra
    # corrective follow-up call was made for the malformed file list.
    assert llm.complete.await_count == 3
    # 2026-08-15 external review, Finding 8: the sibling-content budget must
    # actually reach DeveloperAgent, scaled to the active (here: primary,
    # since attempt 1 never escalates) model's real context window - not
    # silently left unset.
    assert first_call_kwargs["sibling_content_budget"] == _reserve_sibling_content_budget(cfg.llm.context_window)

def test_is_near_duplicate_rule_catches_real_observed_rephrasings():
    """Regression test using the actual duplicate pairs observed live: qpid/rules.txt
    accumulated ~11 near-duplicate rules across one session's repeated skill-gap
    prompts against overlapping reference material - each pair exact-string-distinct
    (so the pre-existing `r not in existing` check missed all of them) but restating
    the identical fact in shorter, differently-worded form."""
    original_model_version = (
        "The initial configuration JSON's \"modelVersion\" field is the broker's internal "
        "domain-model schema version, NOT the qpid-broker-core artifact/release version - "
        "do not set it to match the broker-core version (e.g. \"9.2\" for broker-core 9.2.1). "
        "Use \"8.0\", which is what qpid-broker-core itself ships as its own default "
        "initial-config.json across its 8.x/9.x/10.x releases. A mismatched modelVersion "
        "fails with \"IllegalConfigurationException: No phase upgrader for version X\" during "
        "SystemLauncher.startup() - this does NOT throw from startup() itself, so the broker "
        "silently never binds its AMQP port and any client connection attempt fails with a "
        "plain connection-refused error that gives no hint the root cause was the config file."
    )
    dup_model_version = (
        "Use modelVersion \"8.0\" in the initial configuration JSON, not the qpid-broker-core "
        "artifact version (e.g., do not use \"9.2\" for broker-core 9.2.1)."
    )
    assert dup_model_version not in [original_model_version]  # exact-string check would miss it
    assert _is_near_duplicate_rule(dup_model_version, [original_model_version])

    original_alias = (
        "The AMQP port in the initial configuration JSON must declare \"virtualhostaliases\" "
        "including a {\"type\": \"defaultAlias\"} entry. Without it, the client's AMQP "
        "Open-frame hostname (defaulting to whatever host is in the connection URI, e.g. "
        "\"localhost\") can never resolve to any virtualhost regardless of what that "
        "virtualhost is named - fails with \"JmsResourceNotFoundException: Unknown hostname "
        "in connection open\" even though the broker itself started and the port opened "
        "without error."
    )
    dup_alias = (
        "The AMQP port definition in the initial configuration JSON MUST include a "
        "virtualhostaliases entry with a defaultAlias, or client connections will fail to "
        "resolve the virtualhost."
    )
    assert _is_near_duplicate_rule(dup_alias, [original_alias])

    original_url = (
        "\"initialConfigurationLocation\" must be a real java.net.URL string, obtained via "
        "YourClass.class.getClassLoader().getResource(\"qpid-initial-config.json\")."
        "toExternalForm() (resolves to a file:/jar: URL the JDK understands). Do NOT build it "
        "as \"classpath:qpid-initial-config.json\" or new URL(\"classpath:...\") - "
        "\"classpath:\" is a Spring-only convention that plain java.net.URL does not recognize."
    )
    dup_url = (
        "The initial configuration JSON file must be loaded via a java.net.URL obtained using "
        "YourClass.class.getClassLoader().getResource(\"qpid-initial-config.json\")."
        "toExternalForm()."
    )
    assert _is_near_duplicate_rule(dup_url, [original_url])

def test_is_near_duplicate_rule_does_not_flag_genuinely_different_rules():
    rule_a = (
        "\"Red Hat Qpid MRG\" and \"Red Hat AMQ\" (classic messaging line) both refer to "
        "Apache Qpid - use genuine Apache Qpid Broker-J and qpid-jms, not ActiveMQ Artemis, "
        "even though both speak AMQP."
    )
    rule_b = (
        "Use org.apache.qpid:qpid-jms-client for the JMS client - version 1.16.0 for "
        "javax.jms (JMS 2.0), or 2.10.0 for jakarta.jms (Jakarta Messaging 3.1). Match "
        "whichever javax/jakarta convention the rest of the Spring project uses; do not mix "
        "the two."
    )
    assert not _is_near_duplicate_rule(rule_b, [rule_a])

def test_is_near_duplicate_rule_ignores_short_rules_below_min_words():
    assert not _is_near_duplicate_rule("Use version 9.2.1.", ["Use version 9.2.1 always."])

def test_normalize_written_filepath_passes_through_relative_path():
    assert normalize_written_filepath("src/app.py", "/workspace") == "src/app.py"

def test_normalize_written_filepath_makes_absolute_path_relative():
    assert normalize_written_filepath("/workspace/app.py", "/workspace") == "app.py"

def test_normalize_written_filepath_rejects_path_escaping_workspace():
    # An absolute path outside the workspace root must not resolve to a "../.." path
    # that would write outside the sandbox - reject it outright.
    assert normalize_written_filepath("/elsewhere/secret.py", "/workspace") is None

def test_normalize_written_filepath_rejects_empty():
    assert normalize_written_filepath("", "/workspace") is None

def test_resolve_run_command_substitutes_when_python_unresolvable():
    with patch("shutil.which", return_value=None):
        assert _resolve_run_command(["python", "main.py"]) == [sys.executable, "main.py"]

def test_resolve_run_command_leaves_command_alone_when_python_resolvable():
    with patch("shutil.which", return_value="/usr/bin/python"):
        assert _resolve_run_command(["python", "main.py"]) == ["python", "main.py"]

def test_resolve_run_command_ignores_non_python_commands():
    with patch("shutil.which", return_value=None):
        assert _resolve_run_command(["python3", "main.py"]) == ["python3", "main.py"]
        assert _resolve_run_command(["node", "main.js"]) == ["node", "main.js"]

def test_resolve_run_command_handles_empty_command():
    assert _resolve_run_command([]) == []

def test_downgrade_ungrounded_goal_explicit_commands_leaves_grounded_claim_alone():
    """The common, legitimate case: the goal genuinely names the command, so
    the executable appears in the goal text - must NOT be downgraded (no
    false positive on the ordinary case)."""
    judgment = {
        "command_source": "goal_explicit",
        "run_commands": [["python", "app.py"]],
        "should_run": True,
        "success_criteria": "prints hi",
    }
    goal = "Run with python app.py; it should print hi"
    result = downgrade_ungrounded_goal_explicit_commands(judgment, goal)
    assert result["command_source"] == "goal_explicit"
    assert result is judgment  # unchanged input returned as-is, not a copy

def test_downgrade_ungrounded_goal_explicit_commands_downgrades_ungrounded_claim():
    """Independent brutal review finding #2: a model claiming
    command_source=goal_explicit for a command whose executable never
    appears anywhere in the goal text must be downgraded to "inferred" so
    the human-in-the-loop approval gate actually fires - the claim is
    self-reported by the same untrusted JSON response as everything else,
    with nothing else checking it."""
    judgment = {
        "command_source": "goal_explicit",
        "run_commands": [["curl", "http://internal-service/admin"]],
        "should_run": True,
        "success_criteria": "returns 200",
    }
    goal = "Build a Python script that prints the current time."
    result = downgrade_ungrounded_goal_explicit_commands(judgment, goal)
    assert result["command_source"] == "inferred"
    # Original input must never be mutated - a new dict is returned.
    assert judgment["command_source"] == "goal_explicit"
    assert result is not judgment

def test_downgrade_ungrounded_goal_explicit_commands_case_insensitive():
    judgment = {"command_source": "goal_explicit", "run_commands": [["python", "app.py"]]}
    goal = "Run with Python app.py"
    result = downgrade_ungrounded_goal_explicit_commands(judgment, goal)
    assert result["command_source"] == "goal_explicit"

def test_downgrade_ungrounded_goal_explicit_commands_uses_executable_basename():
    """A full/absolute path executable (e.g. after some upstream resolution)
    must still match against the goal naming just the bare tool name."""
    judgment = {"command_source": "goal_explicit", "run_commands": [["/usr/bin/python3", "app.py"]]}
    goal = "Run with python3 app.py"
    result = downgrade_ungrounded_goal_explicit_commands(judgment, goal)
    assert result["command_source"] == "goal_explicit"

def test_downgrade_ungrounded_goal_explicit_commands_multi_command_any_ungrounded_downgrades_all():
    """Multi-step sequence: even if the FIRST command is genuinely grounded,
    one ungrounded command anywhere in the sequence downgrades the whole
    thing - same AND-semantics precedent as extract_contract_verdict()'s
    multi-step FAIL handling. Failing toward more approval-gating, not less."""
    judgment = {
        "command_source": "goal_explicit",
        "run_commands": [["python", "app.py", "add"], ["curl", "http://internal/admin"]],
    }
    goal = "Run with python app.py add, then check the result."
    result = downgrade_ungrounded_goal_explicit_commands(judgment, goal)
    assert result["command_source"] == "inferred"

def test_downgrade_ungrounded_goal_explicit_commands_leaves_inferred_alone():
    """Already "inferred" is already the safe/gated state - no-op, and the
    function must not require run_commands to look at a non-goal_explicit
    judgment (e.g. should_run=False, run_commands=None)."""
    judgment = {"command_source": "inferred", "run_commands": None, "should_run": False}
    result = downgrade_ungrounded_goal_explicit_commands(judgment, "Build a library.")
    assert result is judgment
    assert result["command_source"] == "inferred"

def test_resolve_run_command_prefixes_bundle_exec_for_rspec_with_gemfile(tmp_path):
    """Regression test for a real bug found live (2026-08-04 eval harness batch,
    after the run_tests()-side bundle-install fix): RunVerifierAgent.judge()
    inferred a bare `rspec <spec>` run command for a Ruby goal - rspec is a
    Bundler-installed executable, never on a bare system PATH, so the run failed
    immediately with 'No such file or directory: rspec' regardless of whether the
    generated code was correct (confirmed - it was). A Gemfile's presence is
    ground truth this project's gems are Bundler-managed."""
    (tmp_path / "Gemfile").write_text("source 'https://rubygems.org'\ngem 'rspec'\n")
    assert _resolve_run_command(["rspec", "spec/word_count_spec.rb"], str(tmp_path)) == [
        "bundle", "exec", "rspec", "spec/word_count_spec.rb"
    ]

def test_resolve_run_command_leaves_rspec_alone_without_a_gemfile(tmp_path):
    # No Gemfile means nothing for `bundle exec` to act on - same reasoning as
    # PolymorphicValidator.run_tests()'s own Gemfile guard.
    assert _resolve_run_command(["rspec", "spec/foo_spec.rb"], str(tmp_path)) == ["rspec", "spec/foo_spec.rb"]

def test_resolve_run_command_does_not_double_prefix_existing_bundle_exec(tmp_path):
    (tmp_path / "Gemfile").write_text("source 'https://rubygems.org'\n")
    assert _resolve_run_command(["bundle", "exec", "rspec"], str(tmp_path)) == ["bundle", "exec", "rspec"]

def test_resolve_run_command_adds_dash_e_for_maven():
    """Regression test for a real bug caught live: a goal-explicit command like
    'mvn -q compile exec:java ...' (many skills recommend -q so
    System.out.println output isn't buried in build noise) makes Maven suppress
    the actual cause of a runtime failure - only exec-maven-plugin's generic
    wrapper message reaches Kriya's retry loop, which then has zero diagnostic
    signal and just guesses. -e must always be injected so failures carry a
    full stack trace."""
    assert _resolve_run_command(
        ["mvn", "-q", "compile", "exec:java", "-Dexec.mainClass=Foo"]
    ) == ["mvn", "-e", "-q", "compile", "exec:java", "-Dexec.mainClass=Foo"]

def test_resolve_run_command_does_not_duplicate_dash_e_for_maven():
    assert _resolve_run_command(["mvn", "-e", "-q", "test"]) == ["mvn", "-e", "-q", "test"]

def test_resolve_run_command_ignores_non_maven_commands_for_dash_e():
    assert _resolve_run_command(["gradle", "run"]) == ["gradle", "run"]
    assert _resolve_run_command(["node", "main.js"]) == ["node", "main.js"]

_EXEC_EXEC_SHAPED_POM = """<project>
  <build>
    <plugins>
      <plugin>
        <groupId>org.codehaus.mojo</groupId>
        <artifactId>exec-maven-plugin</artifactId>
        <configuration>
          <executable>java</executable>
          <arguments>
            <argument>--add-opens=java.base/java.nio=ALL-UNNAMED</argument>
            <argument>-classpath</argument>
            <classpath/>
            <argument>${exec.mainClass}</argument>
          </arguments>
        </configuration>
      </plugin>
    </plugins>
  </build>
</project>
"""

_EXEC_JAVA_SHAPED_POM = """<project>
  <build>
    <plugins>
      <plugin>
        <groupId>org.codehaus.mojo</groupId>
        <artifactId>exec-maven-plugin</artifactId>
        <configuration>
          <mainClass>${exec.mainClass}</mainClass>
        </configuration>
      </plugin>
    </plugins>
  </build>
</project>
"""

def test_resolve_run_command_corrects_exec_java_to_exec_exec_when_pom_needs_it(tmp_path):
    """Regression test for a real bug found via a live golden-use-case run
    (Ignite Spring XML app): RunVerifierAgent.judge() only ever sees the
    Architect's own already-minimized design text (confirmed live: just a
    bare file list, not the richer Planner plan that correctly said
    "exec:exec") - never the matched skill's rules or the actual pom.xml
    written - so it inferred `mvn -e exec:java` against a pom.xml written in
    the exec:exec-only <arguments>/<classpath/> shape (correctly, per the
    ignite-java17 skill's own --add-opens guidance). exec:java's "arguments"
    parameter is a plain String[] and cannot parse the bare <classpath/>
    placeholder at all - it crashed identically on every retry ("Cannot
    store value into array: ... cannot cast ... to ... String"), burning the
    entire retry budget on a problem that was never in the generated code.
    The real pom.xml is ground truth for which goal will work - checking it
    directly doesn't depend on skill/plan/design guidance surviving intact
    through the pipeline."""
    (tmp_path / "pom.xml").write_text(_EXEC_EXEC_SHAPED_POM)
    assert _resolve_run_command(
        ["mvn", "-e", "exec:java"], str(tmp_path)
    ) == ["mvn", "-e", "exec:exec"]

def test_resolve_run_command_leaves_exec_java_alone_when_pom_does_not_need_exec_exec(tmp_path):
    (tmp_path / "pom.xml").write_text(_EXEC_JAVA_SHAPED_POM)
    assert _resolve_run_command(
        ["mvn", "-e", "exec:java"], str(tmp_path)
    ) == ["mvn", "-e", "exec:java"]

def test_resolve_run_command_skips_exec_goal_correction_without_workspace_path(tmp_path):
    # Same exec:exec-shaped pom as the fix-triggering test above, but no
    # workspace_path given - must not attempt the check at all (also proves
    # every pre-existing single-arg call site/test keeps working unchanged).
    (tmp_path / "pom.xml").write_text(_EXEC_EXEC_SHAPED_POM)
    assert _resolve_run_command(["mvn", "-e", "exec:java"]) == ["mvn", "-e", "exec:java"]

def test_resolve_run_command_handles_missing_pom_gracefully(tmp_path):
    assert _resolve_run_command(
        ["mvn", "-e", "exec:java"], str(tmp_path)
    ) == ["mvn", "-e", "exec:java"]

def test_resolve_run_command_exec_goal_correction_ignores_non_maven_commands(tmp_path):
    (tmp_path / "pom.xml").write_text(_EXEC_EXEC_SHAPED_POM)
    assert _resolve_run_command(["gradle", "exec:java"], str(tmp_path)) == ["gradle", "exec:java"]

def _write_java_class(tmp_path, rel_path, package, class_name, with_main=True, main_signature="String[] args"):
    full = tmp_path / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    body = f"public static void main({main_signature}) {{ }}" if with_main else ""
    full.write_text(f"package {package};\n\npublic class {class_name} {{\n    {body}\n}}\n")

def test_resolve_maven_main_class_finds_unambiguous_entry_point(tmp_path):
    _write_java_class(tmp_path, "src/main/java/com/example/protocol/Main.java", "com.example.protocol", "Main")
    assert _resolve_maven_main_class(str(tmp_path)) == "com.example.protocol.Main"

def test_resolve_maven_main_class_accepts_varargs_signature(tmp_path):
    _write_java_class(
        tmp_path, "src/main/java/com/example/Main.java", "com.example", "Main",
        main_signature="String... args",
    )
    assert _resolve_maven_main_class(str(tmp_path)) == "com.example.Main"

def test_resolve_maven_main_class_returns_none_when_ambiguous(tmp_path):
    """Regression test for the real bug this whole fix exists for
    (2026-08-11, staged-build stage 2): a stale/wrong pom.xml mainClass
    (ClassNotFoundException: com.example.MainApp) and a completely missing
    one (PluginParameterException: mainClass missing) were both traced to
    RunVerifierAgent.judge() never checking the pom's mainClass claim
    against what was actually generated. But with two real entry points,
    the function must NOT guess - a genuine multi-entry-point project is a
    real possibility, not something to silently pick one of at random."""
    _write_java_class(tmp_path, "src/main/java/com/example/Main.java", "com.example", "Main")
    _write_java_class(tmp_path, "src/main/java/com/example/Second.java", "com.example", "Second")
    assert _resolve_maven_main_class(str(tmp_path)) is None

def test_resolve_maven_main_class_returns_none_with_no_candidates(tmp_path):
    _write_java_class(tmp_path, "src/main/java/com/example/Helper.java", "com.example", "Helper", with_main=False)
    assert _resolve_maven_main_class(str(tmp_path)) is None

def test_resolve_maven_main_class_returns_none_without_src_main_java(tmp_path):
    assert _resolve_maven_main_class(str(tmp_path)) is None

def test_resolve_run_command_appends_dexec_mainclass_for_exec_java(tmp_path):
    """The actual fix: exec:java's mainClass is corrected using ground truth
    from the real generated source tree, via -Dexec.mainClass= (which takes
    precedence over pom.xml's own <configuration><mainClass>), rather than
    trusting whatever the model wrote in pom.xml."""
    _write_java_class(tmp_path, "src/main/java/com/example/protocol/Main.java", "com.example.protocol", "Main")
    assert _resolve_run_command(["mvn", "-e", "exec:java"], str(tmp_path)) == [
        "mvn", "-e", "exec:java", "-Dexec.mainClass=com.example.protocol.Main",
    ]

def test_resolve_run_command_does_not_override_existing_dexec_mainclass(tmp_path):
    _write_java_class(tmp_path, "src/main/java/com/example/Main.java", "com.example", "Main")
    assert _resolve_run_command(
        ["mvn", "-e", "exec:java", "-Dexec.mainClass=com.example.Other"], str(tmp_path)
    ) == ["mvn", "-e", "exec:java", "-Dexec.mainClass=com.example.Other"]

def test_resolve_run_command_leaves_exec_java_alone_when_main_class_ambiguous(tmp_path):
    _write_java_class(tmp_path, "src/main/java/com/example/Main.java", "com.example", "Main")
    _write_java_class(tmp_path, "src/main/java/com/example/Second.java", "com.example", "Second")
    assert _resolve_run_command(["mvn", "-e", "exec:java"], str(tmp_path)) == ["mvn", "-e", "exec:java"]

def test_resolve_run_command_mainclass_correction_does_not_apply_to_exec_exec(tmp_path):
    # Out of scope by design - exec:exec's main class is a positional
    # <arguments> element, not a separately overridable parameter.
    _write_java_class(tmp_path, "src/main/java/com/example/Main.java", "com.example", "Main")
    assert _resolve_run_command(["mvn", "-e", "exec:exec"], str(tmp_path)) == ["mvn", "-e", "exec:exec"]

def test_extract_planner_code_blocks_matches_full_paths():
    plan = (
        "### pom.xml\n```xml\n<project>pom content</project>\n```\n\n"
        "### src/main/java/com/example/protocol/Protocol.java\n"
        "```java\npackage com.example.protocol;\npublic class Protocol {}\n```\n\n"
        "### src/main/java/com/example/protocol/ProtocolParser.java\n"
        "```java\npackage com.example.protocol;\npublic class ProtocolParser {}\n```\n"
    )
    expected = [
        "pom.xml",
        "src/main/java/com/example/protocol/Protocol.java",
        "src/main/java/com/example/protocol/ProtocolParser.java",
    ]
    result = extract_planner_code_blocks(plan, expected)
    assert set(result.keys()) == set(expected)
    assert "public class ProtocolParser {}" in result["src/main/java/com/example/protocol/ProtocolParser.java"]

def test_extract_planner_code_blocks_resolves_closest_match_not_first_found():
    """Regression test for a real bug caught before this feature ever shipped:
    matching against an unordered set and stopping at the first substring hit
    let an EARLIER file's own heading (still within the lookback window once
    2+ files are shown close together) steal a LATER block's match - e.g.
    Protocol.java's heading, mentioned before ProtocolParser.java's own
    heading+fence, could silently claim ProtocolParser's content instead.
    Fixed by always preferring whichever candidate's mention sits closest
    (rightmost) to the fence, checked with a 3-file fixture where a naive
    first-match approach demonstrably picks the wrong file."""
    plan = (
        "### src/main/java/com/example/protocol/Protocol.java\n"
        "```java\npublic class Protocol {}\n```\n\n"
        "### src/main/java/com/example/protocol/ProtocolParser.java\n"
        "```java\npublic class ProtocolParser {}\n```\n"
    )
    expected = [
        "src/main/java/com/example/protocol/Protocol.java",
        "src/main/java/com/example/protocol/ProtocolParser.java",
    ]
    result = extract_planner_code_blocks(plan, expected)
    assert result["src/main/java/com/example/protocol/Protocol.java"].strip() == "public class Protocol {}"
    assert result["src/main/java/com/example/protocol/ProtocolParser.java"].strip() == "public class ProtocolParser {}"

def test_extract_planner_code_blocks_basename_fallback():
    plan = "### Main.java\n```java\npublic class Main {}\n```\n"
    result = extract_planner_code_blocks(plan, ["src/main/java/com/example/Main.java"])
    assert result == {"src/main/java/com/example/Main.java": "public class Main {}\n"}

def test_extract_planner_code_blocks_skips_empty_fences():
    plan = "### Foo.java\n```java\n```\n"
    assert extract_planner_code_blocks(plan, ["Foo.java"]) == {}

def test_extract_planner_code_blocks_ignores_fences_matching_no_expected_file():
    plan = (
        "Here is an example JMS message payload:\n```json\n{\"foo\": \"bar\"}\n```\n\n"
        "### Foo.java\n```java\npublic class Foo {}\n```\n"
    )
    result = extract_planner_code_blocks(plan, ["Foo.java"])
    assert list(result.keys()) == ["Foo.java"]

def test_extract_planner_code_blocks_partial_coverage_returns_only_matched_files():
    plan = "### Foo.java\n```java\npublic class Foo {}\n```\n"
    result = extract_planner_code_blocks(plan, ["Foo.java", "Bar.java"])
    assert list(result.keys()) == ["Foo.java"]

def test_extract_planner_code_blocks_empty_inputs():
    assert extract_planner_code_blocks("", ["Foo.java"]) == {}
    assert extract_planner_code_blocks("some plan text", []) == {}

def test_extract_planner_code_blocks_rejects_non_java_content_for_java_file():
    """Regression test for a real bug found live (2026-08-12 eval harness
    batch, ignite_qpid_person): the Planner mentioned IgniteQpidPersonDemo.java,
    then later - within the lookback window - wrote a fenced "how to run this"
    snippet quoting the qpid/ignite-java17 skill's own documented run-command
    example, not real Java source. Extraction had no way to tell a run-command
    snippet apart from real code, so it got reused as the file's ENTIRE
    content, and only the (much more expensive) compile gate caught it
    afterward. A single-line, class-less fence for a .java file must be
    rejected - the caller's own all-or-nothing check then falls through to a
    real Developer generation instead of writing this straight to disk."""
    plan = (
        "### src/main/java/com/example/IgniteQpidPersonDemo.java\n"
        "Run it via:\n"
        "```\nmvn -q compile exec:exec -Dexec.mainClass=com.example.IgniteQpidPersonDemo\n```\n"
    )
    result = extract_planner_code_blocks(plan, ["src/main/java/com/example/IgniteQpidPersonDemo.java"])
    assert result == {}

def test_extract_planner_code_blocks_still_accepts_real_java_content():
    """Sibling to the rejection test above - confirms the new plausibility
    check doesn't false-positive on genuinely valid Java content that merely
    lacks a top-level class/interface/enum/record on this particular fence
    (e.g. a real class declaration is still accepted normally)."""
    plan = (
        "### Foo.java\n```java\npackage com.example;\npublic class Foo {}\n```\n"
    )
    result = extract_planner_code_blocks(plan, ["Foo.java"])
    assert result == {"Foo.java": "package com.example;\npublic class Foo {}\n"}

def test_extract_planner_code_blocks_rejects_syntactically_invalid_python():
    """Regression test for a real bug found live (2026-08-13 eval harness,
    python_greeter): the Planner's fenced greet.py content had Kriya's own
    "[VERIFICATION] PASS" runtime-verification marker embedded as a bare,
    unquoted line rather than inside a print() call - syntactically invalid,
    but it still contained real `def`/`print` elsewhere, so the .java-style
    keyword-presence check this table originally shipped with would have
    missed it entirely (unlike Java, valid Python has no required top-level
    keyword to search for). The reused, unreviewed content then took the
    Developer retry loop 5 attempts to recover from live. A .py fence that
    doesn't actually parse must be rejected the same way a .java fence that
    doesn't look like Java is."""
    plan = (
        "### greet.py\n```python\n"
        "def greet(name: str) -> str:\n"
        "    return f\"Hello, {name}!\"\n\n"
        "[VERIFICATION] PASS\n"
        "print(greet('World'))\n"
        "```\n"
    )
    result = extract_planner_code_blocks(plan, ["greet.py"])
    assert result == {}

def test_extract_planner_code_blocks_still_accepts_real_python_content():
    """Sibling to the rejection test above - confirms the new ast.parse()-based
    plausibility check doesn't false-positive on genuinely valid Python (e.g.
    a file with no class/def at all, just top-level statements, is still
    accepted normally - unlike Java's keyword check, Python has no required
    keyword to look for)."""
    plan = (
        "### greet.py\n```python\n"
        "def greet(name: str) -> str:\n"
        "    return f\"Hello, {name}!\"\n\n"
        "print(greet('World'))\n"
        "```\n"
    )
    result = extract_planner_code_blocks(plan, ["greet.py"])
    assert result == {
        "greet.py": "def greet(name: str) -> str:\n    return f\"Hello, {name}!\"\n\nprint(greet('World'))\n"
    }

def test_extract_planner_code_blocks_rejects_run_command_for_xml_file():
    """Regression test for the identical failure shape as the .java test
    above, recurring live 2026-08-17 (ignite_qpid_person, run b-10k) through
    the one gap that test's own fix never covered: .xml had no entry in
    _MIN_PLAUSIBLE_CODE_CHECK, so the same "mvn -q compile exec:exec ..."
    run-command snippet got silently accepted as ignite-config.xml's entire
    content, causing "malformed XML: syntax error: line 1, column 0" - the
    much more expensive structural-corruption/compile gate had to catch it
    instead of extraction rejecting it up front."""
    plan = (
        "### src/main/resources/ignite-config.xml\n"
        "Run it via:\n"
        "```\nmvn -q compile exec:exec -Dexec.mainClass=com.example.PersonApp\n```\n"
    )
    result = extract_planner_code_blocks(plan, ["src/main/resources/ignite-config.xml"])
    assert result == {}

def test_extract_planner_code_blocks_still_accepts_real_xml_content():
    """Sibling to the rejection test above, AND confirms the real incident's
    exact shape: a valid XML fence followed by an unrelated run-command fence
    closer to the heading must not let the later, invalid fence win via
    last-block-wins - the earlier, genuinely valid XML must still be the one
    returned."""
    plan = (
        "### ignite-config.xml\n"
        "```xml\n<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
        "<beans xmlns=\"http://www.springframework.org/schema/beans\">\n"
        "    <bean id=\"ignite.cfg\" class=\"org.apache.ignite.configuration.IgniteConfiguration\"/>\n"
        "</beans>\n```\n"
        "Run instructions for ignite-config.xml:\n"
        "```bash\nmvn -q compile exec:exec -Dexec.mainClass=com.example.PersonApp\n```\n"
    )
    result = extract_planner_code_blocks(plan, ["ignite-config.xml"])
    assert result.get("ignite-config.xml", "").strip().startswith("<?xml")

def test_extract_planner_code_blocks_rejects_run_command_for_json_file():
    # Same shape, .json - added alongside .xml for the same reason (equally
    # common in this pipeline's generated projects, equally guessable via a
    # real parser).
    plan = (
        "### src/main/resources/qpid-initial-config.json\n"
        "Run it via:\n"
        "```\nmvn -q compile exec:exec -Dexec.mainClass=com.example.PersonApp\n```\n"
    )
    result = extract_planner_code_blocks(plan, ["src/main/resources/qpid-initial-config.json"])
    assert result == {}

def test_extract_planner_code_blocks_still_accepts_real_json_content():
    plan = '### config.json\n```json\n{"name": "test"}\n```\n'
    result = extract_planner_code_blocks(plan, ["config.json"])
    assert result == {"config.json": '{"name": "test"}\n'}

@pytest.mark.asyncio
async def test_workflow_uses_per_role_model_config(tmp_path):
    """Configured agent_llms overrides must actually reach each role's real
    llm.complete() call - proving the config flows from AppConfig through
    WorkflowEngine's constructed agents, not just that the config schema parses."""
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.agent_llms.planner.llm = LLMConfig(model="devstral-small-2:24b")
    cfg.agent_llms.reviewer.llm = LLMConfig(model="devstral-small-2:24b")
    # architect is deliberately left unset - should use the default call shape.
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",     # Planner
        "Design: Write app.py",   # Architect
        '[{"filepath": "app.py", "content": "print(1)\\n"}]',  # Developer
        "Review: Approved"        # Reviewer
    ])

    we = WorkflowEngine(kernel, llm)
    res = await we.run_generation_workflow(goal="Create math library", workspace_path=str(tmp_path))

    assert res["quality_gates_passed"] is True
    planner_kwargs = llm.complete.await_args_list[0].kwargs
    architect_kwargs = llm.complete.await_args_list[1].kwargs
    reviewer_kwargs = llm.complete.await_args_list[3].kwargs
    assert planner_kwargs.get("model_override") == "devstral-small-2:24b"
    assert "model_override" not in architect_kwargs  # unset role -> today's default call shape
    assert reviewer_kwargs.get("model_override") == "devstral-small-2:24b"

@pytest.mark.asyncio
async def test_workflow_successful_run(tmp_path):
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write math.py",
        # design's "math.py" mention activates known_target_files on attempt 1,
        # skipping straight to a plain per-file content completion.
        "def add(a,b):\n    return a+b",
        "Review: Approved"
    ])

    we = WorkflowEngine(kernel, llm)

    res = await we.run_generation_workflow(
        goal="Create math library",
        workspace_path=str(tmp_path)
    )

    assert res["quality_gates_passed"] is True
    assert "math.py" in res["files"]
    assert os.path.exists(os.path.join(tmp_path, "math.py"))
    assert res["review"] == "Review: Approved"
    with open(os.path.join(tmp_path, "math.py")) as f:
        assert "def add(a,b):" in f.read()

@pytest.mark.asyncio
async def test_workflow_syntax_error_auto_debugging_loop(tmp_path):
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write math.py",
        # design's "math.py" mention activates known_target_files on attempt 1,
        # skipping straight to a plain per-file content completion (broken: missing colon).
        "def add(a,b)\n    return a+b",
        # Syntax error implicates math.py -> targeted retry, also known_target_files,
        # also a plain per-file content completion (fixed this time).
        "def add(a,b):\n    return a+b",
        "Review: Approved"
    ])

    we = WorkflowEngine(kernel, llm)

    res = await we.run_generation_workflow(
        goal="Create math library with auto-debugging",
        workspace_path=str(tmp_path)
    )
    
    assert res["quality_gates_passed"] is True
    assert "math.py" in res["files"]
    
    # Check that file was rewritten with correct code
    with open(os.path.join(tmp_path, "math.py"), "r") as f:
        content = f.read()
    assert "def add(a,b):" in content


@pytest.mark.asyncio
async def test_workflow_incomplete_generation_triggers_retry(tmp_path):
    """A Developer Agent that only writes a subset of the design's planned files
    (e.g. only pom.xml instead of pom.xml + 6 source files) must not be accepted as
    a passing run just because what little it wrote happens to compile."""
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write math.py and helper.py",
        '[{"filepath": "math.py", "content": "def add(a,b):\\n    return a+b"}]',
        '[{"filepath": "math.py", "content": "def add(a,b):\\n    return a+b"}, '
        '{"filepath": "helper.py", "content": "def helper():\\n    pass"}]',
        "Review: Approved"
    ])

    we = WorkflowEngine(kernel, llm)

    res = await we.run_generation_workflow(
        goal="Create math library with a helper module",
        workspace_path=str(tmp_path)
    )

    assert res["quality_gates_passed"] is True
    assert "math.py" in res["files"]
    assert "helper.py" in res["files"]
    assert os.path.exists(os.path.join(tmp_path, "helper.py"))


@pytest.mark.asyncio
async def test_workflow_missing_file_recovery_lets_model_resolve_nested_path(tmp_path):
    """Regression test for a real bug caught live (qwen3.6 Qpid+Ignite validation):
    last_missing_files (from find_missing_expected_files) is always bare basenames,
    since the Architect's design text doesn't carry directory paths - "helper.py",
    never "pkg/helper.py". The recovery path resolves this itself now
    (_resolve_file_paths_from_design, searching the design text for a fuller path
    mention ending in that basename) rather than trusting either a bare basename
    or the model's own file-list response - the latter was also confirmed live to
    reliably return only ONE of several explicitly-named missing files, silently
    dropping the rest and burning the whole retry budget without ever recovering
    them. So this attempt's file-list call never happens at all (known_target_files
    bypasses it) - only a single per-file content completion for the resolved path."""
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write math.py and pkg/helper.py",
        '[{"filepath": "math.py", "content": "def add(a,b):\\n    return a+b"}]',
        "def helper():\n    pass",
        "Review: Approved"
    ])

    we = WorkflowEngine(kernel, llm)

    res = await we.run_generation_workflow(
        goal="Create math library with a helper module in a package",
        workspace_path=str(tmp_path)
    )

    assert res["quality_gates_passed"] is True
    assert os.path.exists(os.path.join(tmp_path, "pkg", "helper.py"))
    assert not os.path.exists(os.path.join(tmp_path, "helper.py"))


@pytest.mark.asyncio
async def test_workflow_fallback_chain(tmp_path):
    from kriya.config import FallbackModelConfig
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.llm_chain = [
        FallbackModelConfig(model="fallback-1"),
        FallbackModelConfig(model="fallback-2")
    ]
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    
    model_overrides = []

    async def mock_complete(*args, **kwargs):
        model_overrides.append(kwargs.get("model_override"))
        n = len(model_overrides)
        if n == 1:
            return "Step 1: Write code"
        elif n == 2:
            return "Design: Write math.py"
        elif n == 3:
            # Attempt 1 (full-set, primary model, retry_count == 0): design's
            # "math.py" mention activates known_target_files, skipping the
            # file-list call entirely - a plain per-file text completion.
            return "def add(a,b)\n    return a+b"
        elif n in (4, 5, 6):
            # 3 targeted retries (also primary model - targeted retries never
            # escalate) - all still broken, to exhaust the targeted budget
            # before the fallback chain ever gets a chance to run.
            # known_target_files (extract_implicated_files already knows it's
            # math.py) skips the file-list call entirely here, so this is a
            # plain per-file text completion, not a JSON file-list response.
            return "def add(a,b)\n    return a+b"
        elif n == 7:
            # Targeted budget now exhausted - a one-shot fallback-targeted fix
            # (fallback-1, still scoped to just math.py, no file-list call
            # needed) gets tried before the expensive full-set path. Also
            # broken here, so the run falls through to a real full-set
            # escalation next.
            return "def add(a,b)\n    return a+b"
        elif n == 8:
            # Full-set path, still escalated to fallback-1 (retry_count is
            # still only 1 - only attempt 1 ever incremented it; the
            # fallback-targeted attempt deliberately doesn't touch retry_count).
            # Fixed this time - Step 1 + content in one shot.
            return '[{"filepath": "math.py", "content": "def add(a,b):\\n    return a+b"}]'
        elif n == 9:
            return '[{"category": "Rules", "value": "Avoid missing colon in function definition.", "quote": "SyntaxError: expected \':\'"}]'
        else:
            return "Review: Approved"

    llm.complete = mock_complete

    we = WorkflowEngine(kernel, llm)
    cfg.paths.skills = str(tmp_path / "skills")

    res = await we.run_generation_workflow(
        goal="Create math library with fallback chain",
        workspace_path=str(tmp_path)
    )

    assert res["quality_gates_passed"] is True
    # The 3 targeted retries (attempts 2-4) must never escalate - only once that
    # budget is exhausted does the one-shot fallback-targeted fix, then the
    # full-set path's own fallback chain, kick in.
    assert model_overrides[3] is None
    assert model_overrides[4] is None
    assert model_overrides[5] is None
    assert model_overrides[6] == "fallback-1"  # one-shot fallback-targeted fix
    assert model_overrides[7] == "fallback-1"  # full-set escalation
    assert "fallback-1" in model_overrides
    
    repo_slug = os.path.basename(tmp_path).lower().strip(".")
    if not repo_slug:
         repo_slug = "root"
    staged_knowledge_file = os.path.join(tmp_path, "skills", f"auto-{repo_slug}", "staged_knowledge.json")
    assert os.path.exists(staged_knowledge_file)
    with open(staged_knowledge_file, "r", encoding="utf-8") as f:
        staged_facts = json.load(f)
    assert any(fact["value"] == "Avoid missing colon in function definition." for fact in staged_facts)

@pytest.mark.asyncio
async def test_workflow_extracts_lesson_from_primary_model_recovery_needing_two_full_set_attempts(tmp_path):
    """Regression test for a real gap found live, 2026-08-11 (the same
    session's "durable verified project facts" backlog item): lesson
    extraction used to be gated on `state.last_model_override and chain` -
    it only ever fired for a fallback-model escalation rescue, never for an
    ordinary primary-model retry that self-corrected, and never at all for a
    project with no llm_chain configured (as here). Broadened to ALSO fire
    when the primary model needed retry_count >= 2 (at least two full
    FULL-SET rewrites) to converge - a real "this was actually hard" bar, not
    just any retry (an earlier, too-broad attempt using bare error_context
    truthiness broke ~20 unrelated tests, each hitting an unplanned extra LLM
    call on a routine single-retry recovery).

    Compile failures are mocked with output that names no known file, so
    last_implicated_files stays empty and the retry loop keeps choosing
    full_set mode (not targeted) both times - the two failures increment
    retry_count to 2 before the third attempt succeeds."""
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    cfg.paths.skills = str(tmp_path / "skills")

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write math.py",
        '[{"category": "Rules", "value": "Always resolve the build dependency graph fully before adding an explicit version pin.", "quote": "Build error: dependency resolution failed."}]',  # lesson extraction
        "Review: Approved",
    ])
    we = WorkflowEngine(kernel, llm)
    we.developer.run_generation = AsyncMock(side_effect=[
        [{"filepath": "math.py", "content": "def add(a,b):\n    return a+b\n"}],
        [{"filepath": "math.py", "content": "def add(a,b):\n    return a+b\n"}],
        [{"filepath": "math.py", "content": "def add(a,b):\n    return a+b\n"}],
    ])

    with patch(
        "kriya.tools.validate.PolymorphicValidator.run_compile_check",
        side_effect=[
            {"success": False, "output": "Build error: dependency resolution failed."},
            {"success": False, "output": "Build error: dependency resolution failed."},
            {"success": True, "output": "Compiled successfully."},
        ],
    ):
        res = await we.run_generation_workflow(
            goal="Create math library, no fallback chain configured",
            workspace_path=str(tmp_path),
        )

    assert res["quality_gates_passed"] is True
    repo_slug = os.path.basename(tmp_path).lower().strip(".") or "root"
    staged_knowledge_file = os.path.join(tmp_path, "skills", f"auto-{repo_slug}", "staged_knowledge.json")
    assert os.path.exists(staged_knowledge_file)
    with open(staged_knowledge_file, "r", encoding="utf-8") as f:
        staged_facts = json.load(f)
    assert any(
        fact["value"] == "Always resolve the build dependency graph fully before adding an explicit version pin."
        for fact in staged_facts
    )

@pytest.mark.asyncio
async def test_workflow_does_not_extract_lesson_from_a_single_targeted_retry(tmp_path):
    """The narrowed bar (retry_count >= 2 or a fallback-model rescue) must
    NOT fire for test_workflow_syntax_error_auto_debugging_loop's shape - a
    single targeted retry on the primary model recovering from one broken
    attempt - matching the original mechanism's intent (a genuinely hard-won
    lesson, not routine single-retry noise)."""
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    cfg.paths.skills = str(tmp_path / "skills")

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write math.py",
        "def add(a,b)\n    return a+b",
        "def add(a,b):\n    return a+b",
        "Review: Approved",
    ])
    we = WorkflowEngine(kernel, llm)
    res = await we.run_generation_workflow(
        goal="Create math library with auto-debugging",
        workspace_path=str(tmp_path),
    )

    assert res["quality_gates_passed"] is True
    repo_slug = os.path.basename(tmp_path).lower().strip(".") or "root"
    staged_file = os.path.join(tmp_path, "skills", f"auto-{repo_slug}", "staged_rules.txt")
    staged_knowledge_file = os.path.join(tmp_path, "skills", f"auto-{repo_slug}", "staged_knowledge.json")
    assert not os.path.exists(staged_file)
    assert not os.path.exists(staged_knowledge_file)

@pytest.mark.asyncio
async def test_workflow_best_of_n_never_activates_without_a_real_sandbox(tmp_path):
    """best_of_n_first_attempt > 1 must be a no-op whenever create_git_worktree()
    fell back to worktree_path == workspace_path (no real isolated sandbox - here
    because tmp_path is never git-initialized) - discarding a candidate with
    nowhere to reset to would leave its files on the real project. Confirms the
    workflow.py dispatch condition, not just run_attempt_with_best_of_n's own
    internal behavior (already covered in tests/test_best_of_n.py)."""
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.autonomy.best_of_n_first_attempt = 3
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write math.py",
        '[{"filepath": "math.py", "content": "def add(a,b):\\n    return a+b"}]',
        "Review: Approved",
    ])
    we = WorkflowEngine(kernel, llm)

    with patch("kriya.workflow.best_of_n.run_attempt_with_best_of_n") as mock_best_of_n:
        res = await we.run_generation_workflow(
            goal="Create a math library",
            workspace_path=str(tmp_path),
        )

    assert res["quality_gates_passed"] is True
    mock_best_of_n.assert_not_called()

@pytest.mark.asyncio
async def test_workflow_best_of_n_succeeds_on_second_independent_candidate(tmp_path):
    """End-to-end happy path with a REAL worktree sandbox (_init_git_repo, unlike
    the no-sandbox test above): the first independent candidate's compile fails,
    Best-of-N discards it and resets, the second candidate's compile succeeds -
    the run must still pass overall, and best_of_n_candidates_tried must record
    exactly one discarded candidate."""
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.autonomy.best_of_n_first_attempt = 2
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write math.py",
        "Review: Approved",
    ])
    we = WorkflowEngine(kernel, llm)
    we.developer.run_generation = AsyncMock(side_effect=[
        [{"filepath": "math.py", "content": "def add(a,b)\n    return a+b\n"}],
        [{"filepath": "math.py", "content": "def add(a,b):\n    return a+b\n"}],
    ])

    with patch(
        "kriya.tools.validate.PolymorphicValidator.run_compile_check",
        side_effect=[
            {"success": False, "output": "SyntaxError: invalid syntax"},
            {"success": True, "output": "Compiled successfully."},
        ],
    ):
        res = await we.run_generation_workflow(
            goal="Create math library",
            workspace_path=str(tmp_path),
        )

    assert res["quality_gates_passed"] is True
    assert we.developer.run_generation.await_count == 2

@pytest.mark.asyncio
async def test_workflow_fallback_targeted_fix_succeeds_before_full_set_regeneration(tmp_path):
    """Regression test for the fallback-targeted-fix step (2026-08-10): once the
    primary-model targeted budget is exhausted, a ONE-SHOT targeted fix on the
    first fallback model must be tried BEFORE any full-set regeneration -
    found live (ignite_qpid_protocol) that jumping straight to a full-set
    regeneration rewrote 6 files (~13 minutes) when only 2 actually needed
    fixing. If this fallback-targeted attempt itself succeeds, the run must
    never reach a full-set regeneration at all."""
    from kriya.config import FallbackModelConfig
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.llm_chain = [FallbackModelConfig(model="fallback-1")]
    cfg.paths.skills = str(tmp_path / "skills")
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write App.java",
        # A genuine fallback-model success triggers lesson extraction
        # (model_override is truthy and a chain is configured).
        "Always double-check byte widths before writing binary fields.",
        "Review: Approved",
    ])

    we = WorkflowEngine(kernel, llm)
    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check") as mock_compile:
        mock_compile.side_effect = [
            {"success": False, "output": "[ERROR] .../App.java:[2,3] cannot find symbol"},  # attempt 1, full-set
            {"success": False, "output": "[ERROR] .../App.java:[2,3] cannot find symbol"},  # attempt 2, targeted
            {"success": False, "output": "[ERROR] .../App.java:[2,3] cannot find symbol"},  # attempt 3, targeted
            {"success": False, "output": "[ERROR] .../App.java:[2,3] cannot find symbol"},  # attempt 4, targeted
            {"success": True, "output": ""},  # attempt 5, fallback-targeted - fixed
        ]
        we.developer.run_generation = AsyncMock(side_effect=[
            [{"filepath": "App.java", "content": "class App {\n  Object x;\n}"}],
            [{"filepath": "App.java", "content": "class App {\n  Object x2;\n}"}],
            [{"filepath": "App.java", "content": "class App {\n  Object x3;\n}"}],
            [{"filepath": "App.java", "content": "class App {\n  Object x4;\n}"}],
            [{"filepath": "App.java", "content": "class App {\n  String x5;\n}"}],
        ])
        res = await we.run_generation_workflow(goal="Create a Java app", workspace_path=str(tmp_path))

    assert res["quality_gates_passed"] is True
    assert we.developer.run_generation.call_count == 5  # never reached a 6th, full-set attempt
    fifth_call_kwargs = we.developer.run_generation.call_args_list[4].kwargs
    assert fifth_call_kwargs["model_override"] == "fallback-1"
    assert fifth_call_kwargs["known_target_files"] == ["App.java"]  # scoped, not a full-set file-list re-derivation
    assert fifth_call_kwargs["extra_fix_instruction"] == DeveloperAgent.SELF_CONSISTENCY_NUDGE

@pytest.mark.asyncio
async def test_workflow_self_correction_loop_resolves_compile_failure(tmp_path):
    """When autonomy.self_correction_loop_enabled is on and a compile gate
    fails, the bounded tool-calling micro-loop (kriya/workflow/self_correction.py)
    gets a chance to fix it in-place before the run falls back to a full-set
    regeneration. If it resolves the failure, the attempt should succeed
    without ever reaching a second Developer generation call - the entire
    point of the feature over today's regenerate-blind retry."""
    from kriya.workflow.self_correction import SelfCorrectionResult

    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.autonomy.self_correction_loop_enabled = True
    cfg.paths.logs = str(tmp_path / "logs")
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write app.py",
        "Review: Approved",
    ])

    we = WorkflowEngine(kernel, llm)
    we.developer.run_generation = AsyncMock(return_value=[
        {"filepath": "app.py", "content": "print('broken'"},
    ])

    fake_result = SelfCorrectionResult(
        resolved=True,
        turns_used=2,
        final_compile_output="compiled OK",
        modified_files={"app.py": "print('broken')"},
        transcript=[{"turn": 0, "tool": "recompile", "arguments": {}, "result": "SUCCESS: compiled OK"}],
    )
    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check", return_value={"success": False, "output": "SyntaxError: unexpected EOF"}), \
         patch("kriya.workflow.self_correction.run_self_correction_loop", new=AsyncMock(return_value=fake_result)) as mock_loop:
        res = await we.run_generation_workflow(goal="Create a python script", workspace_path=str(tmp_path))

    assert res["quality_gates_passed"] is True
    assert we.developer.run_generation.call_count == 1  # never reached a second, full-set regeneration
    mock_loop.assert_called_once()

    db_path = os.path.join(cfg.paths.logs, "traces.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM runs ORDER BY timestamp DESC LIMIT 1").fetchone()
    conn.close()
    gate_outcomes = json.loads(row["gate_outcomes"])
    compile_outcomes = [o for o in gate_outcomes if o.get("type") == "compile"]
    assert len(compile_outcomes) == 1
    assert compile_outcomes[0]["success"] is True
    assert compile_outcomes[0]["self_corrected"] is True
    assert compile_outcomes[0]["self_correction_turns"] == 2

@pytest.mark.asyncio
async def test_workflow_self_correction_loop_exhausts_falls_through_unchanged(tmp_path):
    """If the micro-loop can't resolve the failure within its turn budget,
    the run must fall through to today's unmodified behavior - a full-set
    regeneration on the next attempt - not get stuck or fail differently."""
    from kriya.workflow.self_correction import SelfCorrectionResult

    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.autonomy.self_correction_loop_enabled = True
    cfg.paths.logs = str(tmp_path / "logs")
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write app.py",
        "Review: Approved",
    ])

    we = WorkflowEngine(kernel, llm)
    we.developer.run_generation = AsyncMock(side_effect=[
        [{"filepath": "app.py", "content": "print('broken'"}],
        [{"filepath": "app.py", "content": "print('fixed')"}],
    ])

    fake_transcript = [{"turn": 0, "tool": "recompile", "arguments": {}, "result": "FAILURE: still broken"}]
    fake_result = SelfCorrectionResult(
        resolved=False, turns_used=4, final_compile_output="still broken", modified_files={}, transcript=fake_transcript,
    )
    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check") as mock_compile, \
         patch("kriya.workflow.self_correction.run_self_correction_loop", new=AsyncMock(return_value=fake_result)) as mock_loop:
        mock_compile.side_effect = [
            {"success": False, "output": "SyntaxError: unexpected EOF"},  # attempt 1
            {"success": True, "output": ""},                              # attempt 2, full-set regeneration
        ]
        res = await we.run_generation_workflow(goal="Create a python script", workspace_path=str(tmp_path))

    assert res["quality_gates_passed"] is True
    assert we.developer.run_generation.call_count == 2  # exactly today's unmodified retry behavior
    mock_loop.assert_called_once()

    # Forensics regression test: an exhausted (not just a resolved) loop's
    # transcript must still be persisted, not silently discarded the moment
    # execution falls through to the ordinary QualityGateFailure path -
    # found live (2026-08-12 eval harness batch) that it previously wasn't.
    db_path = os.path.join(cfg.paths.logs, "traces.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM runs ORDER BY timestamp DESC LIMIT 1").fetchone()
    conn.close()
    gate_outcomes = json.loads(row["gate_outcomes"])
    failed_compile_outcomes = [o for o in gate_outcomes if o.get("type") == "compile" and not o["success"]]
    assert len(failed_compile_outcomes) == 1
    assert failed_compile_outcomes[0]["self_correction_attempt"] == {
        "turns_used": 4, "transcript": fake_transcript, "final_compile_output": "still broken",
    }

@pytest.mark.asyncio
async def test_workflow_self_correction_loop_disabled_by_default_zero_new_code_path(tmp_path):
    """With the flag left at its pydantic default (off), a compile failure
    must never even import/invoke the self-correction module - proving the
    flag-off path is a true no-op, not just a discarded result."""
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.paths.logs = str(tmp_path / "logs")
    assert cfg.autonomy.self_correction_loop_enabled is False  # sanity: default, not explicitly set
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write app.py",
        "Review: Approved",
    ])

    we = WorkflowEngine(kernel, llm)
    we.developer.run_generation = AsyncMock(side_effect=[
        [{"filepath": "app.py", "content": "print('broken'"}],
        [{"filepath": "app.py", "content": "print('fixed')"}],
    ])

    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check") as mock_compile, \
         patch("kriya.workflow.self_correction.run_self_correction_loop") as mock_loop:
        mock_compile.side_effect = [
            {"success": False, "output": "SyntaxError: unexpected EOF"},  # attempt 1
            {"success": True, "output": ""},                              # attempt 2, full-set regeneration
        ]
        res = await we.run_generation_workflow(goal="Create a python script", workspace_path=str(tmp_path))

    assert res["quality_gates_passed"] is True
    assert we.developer.run_generation.call_count == 2  # today's unmodified retry flow, unaffected
    mock_loop.assert_not_called()

@pytest.mark.asyncio
async def test_workflow_fallback_targeted_fix_skipped_without_fallback_chain(tmp_path):
    """Without a configured fallback chain, exhausting the targeted budget must
    fall straight through to a plain, primary-model full-set retry - exactly
    today's behavior. use_fallback_targeted requires a non-empty chain, so
    this is a pure regression check, not new behavior."""
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    # No llm_chain configured - the common/default case.
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write App.java",
        '{"files": ["App.java"]}',
        "Review: Approved",
    ])

    we = WorkflowEngine(kernel, llm)
    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check") as mock_compile:
        mock_compile.side_effect = [
            {"success": False, "output": "[ERROR] .../App.java:[2,3] cannot find symbol"},
            {"success": False, "output": "[ERROR] .../App.java:[2,3] cannot find symbol"},
            {"success": False, "output": "[ERROR] .../App.java:[2,3] cannot find symbol"},
            {"success": False, "output": "[ERROR] .../App.java:[2,3] cannot find symbol"},
            {"success": True, "output": ""},
        ]
        we.developer.run_generation = AsyncMock(side_effect=[
            [{"filepath": "App.java", "content": "class App {\n  Object x;\n}"}],
            [{"filepath": "App.java", "content": "class App {\n  Object x2;\n}"}],
            [{"filepath": "App.java", "content": "class App {\n  Object x3;\n}"}],
            [{"filepath": "App.java", "content": "class App {\n  Object x4;\n}"}],
            [{"filepath": "App.java", "content": "class App {\n  String x5;\n}"}],
        ])
        res = await we.run_generation_workflow(goal="Create a Java app", workspace_path=str(tmp_path))

    assert res["quality_gates_passed"] is True
    assert we.developer.run_generation.call_count == 5
    fifth_call_kwargs = we.developer.run_generation.call_args_list[4].kwargs
    assert fifth_call_kwargs.get("model_override") is None
    assert fifth_call_kwargs.get("known_target_files") is None  # full-set: re-derives the file list


@pytest.mark.asyncio
async def test_workflow_cumulative_sandbox_sync(tmp_path):
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    
    # We want attempt 1 to generate file1.py, which compiles fine, but we'll mock compile check or test to fail once.
    # Attempt 2 will generate file2.py.
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write files",
        # Attempt 1: Generates file1.py
        '[{"filepath": "file1.py", "content": "def func1():\\n    return 1"}]',
        # Attempt 2: Generates only file2.py
        '[{"filepath": "file2.py", "content": "def func2():\\n    return 2"}]',
        "Avoid test failure.",
        "Review: Approved"
    ])

    we = WorkflowEngine(kernel, llm)
    
    # Mock validator compile and test checks to trigger a retry
    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check") as mock_compile, \
         patch("kriya.tools.validate.PolymorphicValidator.run_tests") as mock_test:
         
         # Attempt 1 compile succeeds, but test fails
         # Attempt 2 compile succeeds, test succeeds
         mock_compile.return_value = {"success": True, "output": ""}
         mock_test.side_effect = [
             {"success": False, "output": "Test failed"}, # Attempt 1
             {"success": True, "output": ""} # Attempt 2
         ]
         
         res = await we.run_generation_workflow(
             goal="Create library with multiple files",
             workspace_path=str(tmp_path)
         )
         
         assert res["quality_gates_passed"] is True
         # Both files should be in the returned file list
         assert "file1.py" in res["files"]
         assert "file2.py" in res["files"]
         
         # Both files should actually exist in the workspace
         assert os.path.exists(os.path.join(tmp_path, "file1.py"))
         assert os.path.exists(os.path.join(tmp_path, "file2.py"))


@pytest.mark.asyncio
async def test_workflow_full_set_prompt_includes_existing_dependencies_checklist(tmp_path):
    """The full-set retry prompt (used for attempt 1 too) must include an
    explicit 'preserve these existing dependencies' checklist when extending
    a project that already has a pom.xml - showing the model passive
    reference content alone was confirmed live NOT sufficient to stop
    dependency drops recurring across the golden-use-case validation."""
    (tmp_path / "pom.xml").write_text("""<?xml version="1.0" encoding="UTF-8"?>
<project>
    <dependencies>
        <dependency>
            <groupId>org.apache.ignite</groupId>
            <artifactId>ignite-core</artifactId>
            <version>2.18.0</version>
        </dependency>
        <dependency>
            <groupId>org.apache.ignite</groupId>
            <artifactId>ignite-indexing</artifactId>
            <version>2.18.0</version>
        </dependency>
    </dependencies>
</project>""")

    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write pom.xml",
        # A bare string, not JSON: attempt 1's heuristic-extracted
        # expected_files_upfront (["pom.xml"], from the design prose) makes
        # this the SOLE targeted file, so known_target_files bypasses
        # DeveloperAgent's Step-1 file-list query (parse_file_list()) entirely
        # and this response is used directly AS the file's raw content - found
        # live, 2026-08-16, investigating why this test had been silently
        # failing all session as a presumed "environment-dependent" baseline
        # item: it wasn't one. The old JSON-array-shaped fixture value here
        # predates that bypass behavior and was never updated to match -
        # confirmed directly via a standalone repro before touching this test.
        "<project></project>",
        "Review: Approved",
    ])

    we = WorkflowEngine(kernel, llm)

    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check") as mock_compile, \
         patch("kriya.tools.validate.PolymorphicValidator.run_pom_validate", return_value={"success": True, "output": ""}), \
         patch("kriya.tools.validate.PolymorphicValidator.run_tests", return_value={"success": True, "output": ""}):
        mock_compile.return_value = {"success": True, "output": "Maven compilation succeeded."}

        res = await we.run_generation_workflow(
            goal="Add Qpid to the project",
            workspace_path=str(tmp_path)
        )

    assert res["quality_gates_passed"] is True
    developer_prompt = llm.complete.call_args_list[2].args[1]
    assert "Existing Maven dependencies" in developer_prompt
    assert "org.apache.ignite:ignite-indexing" in developer_prompt
    assert "org.apache.ignite:ignite-core" in developer_prompt
    # Regression test for a real bug found live during golden-use-case
    # validation: the preservation checklist only ever protected against
    # DROPPING an existing dependency - nothing stopped the model from ADDING
    # a new, redundant (and in the real case, nonexistent) dependency for a
    # package that was already resolvable via an existing one. A retry added
    # javax.jms:jms:1.1 (removed from Maven Central) for a javax.jms.* import
    # that was already compiling fine via the existing qpid-jms-client
    # dependency alone - both wrong and unnecessary.
    assert "you must NOT add a new, separate dependency" in developer_prompt


def test_build_ecosystem_invariant_block_names_detected_frameworks():
    """Unit test for the pure helper: when the repo analyzer already detected
    real frameworks, name them explicitly - the same specificity that made
    the dependency-preservation checklist effective over a purely generic
    instruction."""
    class FakeRepoModel:
        frameworks = ["Django"]
    block = _build_ecosystem_invariant_block(FakeRepoModel())
    assert "Ecosystem Preservation" in block
    assert "Django" in block
    assert "already detected" in block

def test_build_ecosystem_invariant_block_generic_when_no_frameworks_detected():
    # A fresh repo with nothing to detect yet must still carry the standing
    # invariant - it's unconditional, not gated on repo facts existing.
    class FakeRepoModel:
        frameworks = []
    block = _build_ecosystem_invariant_block(FakeRepoModel())
    assert "Ecosystem Preservation" in block
    assert "already detected" not in block

@pytest.mark.asyncio
async def test_workflow_prompt_includes_ecosystem_invariant_on_first_attempt(tmp_path):
    """Regression test for a real bug found live (2026-08-04 eval harness): a
    Django/Python goal produced Java/Spring Boot code, and a separate Python
    goal invented a Maven-style src/main/src/test layout - neither goal ever
    mentioned Java. Confirmed via traces.db this was NOT skill-content bias
    (zero skills were active for the Django run) - a prompting-level fix is
    the right lever. The invariant must reach the very first attempt, not
    just retries, since attempt 1 is where the drift was actually observed."""
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write views.py",
        "Review: Approved",
    ])
    we = WorkflowEngine(kernel, llm)
    we.developer.run_generation = AsyncMock(return_value=[
        {"filepath": "views.py", "content": "def healthz(request):\n    return {}\n"}
    ])
    res = await we.run_generation_workflow(
        goal="Using Django 5.2, add a minimal view at /healthz", workspace_path=str(tmp_path)
    )
    assert res["quality_gates_passed"] is True
    first_call_kwargs = we.developer.run_generation.call_args_list[0].kwargs
    assert "Ecosystem Preservation" in first_call_kwargs["task_description"]
    assert "Java/Spring/Maven" in first_call_kwargs["task_description"]

@pytest.mark.asyncio
async def test_workflow_prompt_includes_ecosystem_invariant_on_targeted_retry(tmp_path):
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write App.java",
        "Review: Approved",
    ])
    we = WorkflowEngine(kernel, llm)
    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check") as mock_compile:
        mock_compile.side_effect = [
            {"success": False, "output": "[ERROR] .../App.java:[2,3] cannot find symbol"},
            {"success": True, "output": ""},
        ]
        we.developer.run_generation = AsyncMock(side_effect=[
            [{"filepath": "App.java", "content": "class App {\n  Object x;\n}"}],
            [{"filepath": "App.java", "content": "class App {\n  String x;\n}"}],
        ])
        res = await we.run_generation_workflow(goal="Create a Java app", workspace_path=str(tmp_path))
    assert res["quality_gates_passed"] is True
    second_call_kwargs = we.developer.run_generation.call_args_list[1].kwargs
    assert "Ecosystem Preservation" in second_call_kwargs["task_description"]
    # extra_fix_instruction wired to always-on 2026-08-10 (spikes/fix_alignment/'s
    # first real batch: closed the diagnosis-execution gap 3/10 -> 1/10 for a
    # simple fix, did nothing for a complex one, never hurt either) - a
    # targeted retry is exactly where a fix-analysis-driven edit happens.
    assert second_call_kwargs["extra_fix_instruction"] == DeveloperAgent.SELF_CONSISTENCY_NUDGE

@pytest.mark.asyncio
async def test_workflow_prompt_includes_ecosystem_invariant_on_missing_files_retry(tmp_path):
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write app.py and helper.py",
        "Review: Approved",
    ])
    we = WorkflowEngine(kernel, llm)
    we.developer.run_generation = AsyncMock(side_effect=[
        [{"filepath": "app.py", "content": "print(1)"}],
        [{"filepath": "helper.py", "content": "def helper():\n    pass\n"}],
    ])
    res = await we.run_generation_workflow(goal="Create app", workspace_path=str(tmp_path))
    assert res["quality_gates_passed"] is True
    second_call_kwargs = we.developer.run_generation.call_args_list[1].kwargs
    assert "Ecosystem Preservation" in second_call_kwargs["task_description"]
    # Missing-file recovery never passes prior_error_context at all (there's no
    # error to analyze - the file was simply never written), so the
    # fix-analysis-driven nudge is structurally irrelevant here and is
    # deliberately NOT wired at this call site.
    assert "extra_fix_instruction" not in second_call_kwargs


def test_resource_lifecycle_header_names_the_core_pattern():
    # Unlike the ecosystem invariant, this one is a plain constant (no
    # per-repo dynamic content to name) - stack-agnostic generalization of
    # skills/ignite-java17/rules.txt's own start-once/reuse/close-once rule.
    assert "Resource Lifecycle" in RESOURCE_LIFECYCLE_HEADER
    assert "close" in RESOURCE_LIFECYCLE_HEADER.lower()
    assert "try-with-resources" in RESOURCE_LIFECYCLE_HEADER

@pytest.mark.asyncio
async def test_workflow_prompt_includes_resource_lifecycle_on_first_attempt(tmp_path):
    """Regression test: the resource-lifecycle checklist must reach the very
    first attempt, not just retries - the Ignite start/close bug it
    generalizes was a first-attempt mistake, not something only surfacing on
    a retry after a runtime failure."""
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write App.py",
        "Review: Approved",
    ])
    we = WorkflowEngine(kernel, llm)
    we.developer.run_generation = AsyncMock(return_value=[
        {"filepath": "App.py", "content": "def main():\n    pass\n"}
    ])
    res = await we.run_generation_workflow(goal="Write a script", workspace_path=str(tmp_path))
    assert res["quality_gates_passed"] is True
    first_call_kwargs = we.developer.run_generation.call_args_list[0].kwargs
    assert "Resource Lifecycle" in first_call_kwargs["task_description"]
    assert "close" in first_call_kwargs["task_description"].lower()

@pytest.mark.asyncio
async def test_workflow_prompt_includes_resource_lifecycle_on_targeted_retry(tmp_path):
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write App.java",
        "Review: Approved",
    ])
    we = WorkflowEngine(kernel, llm)
    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check") as mock_compile:
        mock_compile.side_effect = [
            {"success": False, "output": "[ERROR] .../App.java:[2,3] cannot find symbol"},
            {"success": True, "output": ""},
        ]
        we.developer.run_generation = AsyncMock(side_effect=[
            [{"filepath": "App.java", "content": "class App {\n  Object x;\n}"}],
            [{"filepath": "App.java", "content": "class App {\n  String x;\n}"}],
        ])
        res = await we.run_generation_workflow(goal="Create a Java app", workspace_path=str(tmp_path))
    assert res["quality_gates_passed"] is True
    second_call_kwargs = we.developer.run_generation.call_args_list[1].kwargs
    assert "Resource Lifecycle" in second_call_kwargs["task_description"]

@pytest.mark.asyncio
async def test_workflow_prompt_includes_resource_lifecycle_on_missing_files_retry(tmp_path):
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write app.py and helper.py",
        "Review: Approved",
    ])
    we = WorkflowEngine(kernel, llm)
    we.developer.run_generation = AsyncMock(side_effect=[
        [{"filepath": "app.py", "content": "print(1)"}],
        [{"filepath": "helper.py", "content": "def helper():\n    pass\n"}],
    ])
    res = await we.run_generation_workflow(goal="Create app", workspace_path=str(tmp_path))
    assert res["quality_gates_passed"] is True
    second_call_kwargs = we.developer.run_generation.call_args_list[1].kwargs
    assert "Resource Lifecycle" in second_call_kwargs["task_description"]

@pytest.mark.asyncio
async def test_workflow_sanitizes_batch_json_content_before_writing_to_disk(tmp_path):
    """Regression test for a real, previously-uncovered gap: DeveloperAgent's
    per-file generation paths (_fill_missing_content) route content through
    DeveloperAgent.sanitize_generated_content, but a batch JSON response's
    content field (DeveloperAgent._normalize_file_entries, used when the
    model returns full file objects in one JSON array) never passed through
    ANY sanitization before this fix - it went straight from parsed JSON to
    disk. Mocking run_generation here stands in for that path (as the other
    workflow-level tests in this file already do for the Developer Agent
    generally) to confirm the workflow's own write loop - not just the
    agent-side paths - now sanitizes any content it receives, regardless of
    which internal path produced it."""
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write App.py",
        "Review: Approved",
    ])
    we = WorkflowEngine(kernel, llm)
    we.developer.run_generation = AsyncMock(return_value=[
        {"filepath": "App.py", "content": "```python\n>> 1: def main():\n    pass\n```"}
    ])
    res = await we.run_generation_workflow(goal="Write a script", workspace_path=str(tmp_path))
    assert res["quality_gates_passed"] is True
    written = (tmp_path / "App.py").read_text()
    assert "```" not in written
    assert ">>" not in written
    assert written == "def main():\n    pass"

@pytest.mark.asyncio
async def test_workflow_sanitizes_batch_json_edits_before_applying(tmp_path):
    """Same gap as above, for the edits path: a batch JSON response's edits
    field (search/replace text) also went straight to apply_anchored_edits
    with zero sanitization before this fix - a model that echoed a gutter
    into an edit supplied this way (not through _split_fix_analysis_edit,
    which already sanitized its own edits) would have produced a guaranteed
    anchor-match failure with no way to recover. Attempt 1 writes the file
    normally and a mocked compile failure forces a targeted retry, so the
    edit's target content is legitimately present in apply_anchored_edits'
    own shown_context guard (mirrors the precedent in
    test_workflow_anchored_edit_failure_captures_filepath, which exercises
    the same edits path but for the mismatch-failure case, not success)."""
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write App.py",
        "Review: Approved",
    ])
    we = WorkflowEngine(kernel, llm)
    # "   1: " (three leading spaces) is Kriya's own non-highlighted
    # context-line gutter (see _build_error_source_context's format string:
    # the two-space placeholder plus its own literal space before {i+1})
    # prepended to the real, unmarked source line "    old()" - exactly the
    # shape a model echoing the gutter back would produce.
    gutter_prefixed_search = "   1: " + "    old()"
    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check") as mock_compile:
        mock_compile.side_effect = [
            {"success": False, "output": "App.py: SyntaxError near old()"},
            {"success": True, "output": ""},
        ]
        we.developer.run_generation = AsyncMock(side_effect=[
            [{"filepath": "App.py", "content": "def main():\n    old()\n"}],
            [{"filepath": "App.py", "edits": [
                {"search": gutter_prefixed_search, "replace": "    new()"}
            ]}],
        ])
        res = await we.run_generation_workflow(goal="Write a script", workspace_path=str(tmp_path))
    assert res["quality_gates_passed"] is True
    written = (tmp_path / "App.py").read_text()
    assert written == "def main():\n    new()\n"


def _latest_trace_row(logs_dir):
    """Read back the most recent row from a test-isolated traces.db (cfg.paths.logs
    pointed at tmp_path), as a dict keyed by column name - avoids every trace-related
    test needing to know the runs table's raw column order."""
    db_path = os.path.join(logs_dir, "traces.db")
    if not os.path.exists(db_path):
        return None
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM runs ORDER BY timestamp DESC LIMIT 1").fetchone()
    conn.close()
    return dict(row) if row else None


@pytest.mark.asyncio
async def test_workflow_persists_intermediate_trace_checkpoint_before_reviewer(tmp_path):
    """Found live, 2026-08-15, forensically investigating a real run whose
    log ended abruptly mid-Reviewer-call: the ONLY trace_logger.log_run()
    call for a normal (non-early-exit) run happened AFTER the Reviewer
    completion - a single LLM call that can itself hang or get killed. When
    that happens, state.gate_outcomes (the full per-attempt forensic history,
    already complete in memory at that point - quality gates having fully
    concluded either way) was entirely lost, leaving nothing for a future
    investigation but raw log-scraping. Confirms an intermediate checkpoint
    (status="in_progress", but WITH real gate_outcomes this time) is visible
    in traces.db the moment Reviewer starts - not just at the very end - and
    that the final call afterward still correctly replaces it with the
    complete, final status."""
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.paths.logs = str(tmp_path / "logs")
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write app.py",
        '[{"filepath": "app.py", "content": "print(1)"}]',
    ])
    we = WorkflowEngine(kernel, llm)

    checkpoint_seen = {}

    async def reviewer_run(*args, **kwargs):
        checkpoint_seen["row"] = _latest_trace_row(cfg.paths.logs)
        return "Review: Approved"

    we.reviewer.run = AsyncMock(side_effect=reviewer_run)

    with patch(
        "kriya.tools.validate.PolymorphicValidator.run_compile_check",
        return_value={"success": True, "output": ""},
    ):
        res = await we.run_generation_workflow(goal="Create app", workspace_path=str(tmp_path))

    assert res["quality_gates_passed"] is True

    checkpoint_row = checkpoint_seen["row"]
    assert checkpoint_row is not None, "no trace row existed yet when Reviewer started"
    assert checkpoint_row["status"] == "in_progress"
    checkpoint_gate_outcomes = json.loads(checkpoint_row["gate_outcomes"])
    assert any(g["type"] == "compile" for g in checkpoint_gate_outcomes)

    final_row = _latest_trace_row(cfg.paths.logs)
    assert final_row["status"] == "success"
    assert final_row["run_id"] == checkpoint_row["run_id"]

@pytest.mark.asyncio
async def test_workflow_stops_retrying_immediately_on_environment_failure(tmp_path):
    """Regression test: a JVM crashing during its own startup (e.g. a startup
    flag unsupported by the actually-resolved JDK) is not a code defect - no
    amount of Developer regeneration can ever fix it. Before this fix, Quality
    Gates treated it exactly like a normal compile failure and burned its full
    retry budget uselessly re-generating code - confirmed as a real, wasteful
    gap during golden-use-case validation: the same JVM-startup crash recurred
    identically across 3 real retry attempts before a human had to intervene."""
    cfg = AppConfig()
    cfg.autonomy.run_verification_enabled = False
    cfg.paths.logs = str(tmp_path / "logs")
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    # Exactly one Developer generation call is provided - if the retry loop
    # incorrectly kept going after the environment failure, a second Developer
    # call would hit StopIteration and fail this test outright.
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write app.py",
        '[{"filepath": "app.py", "content": "print(1)"}]',
        "Review: Approved",
    ])

    we = WorkflowEngine(kernel, llm)

    jvm_error = (
        "Error occurred during initialization of VM\n"
        "java.lang.Error: A command line option has attempted to allow or "
        "enable the Security Manager. Enabling a Security Manager is not "
        "supported."
    )
    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check") as mock_compile:
        mock_compile.return_value = {"success": False, "output": jvm_error}

        res = await we.run_generation_workflow(
            goal="Create app",
            workspace_path=str(tmp_path)
        )

    assert res["quality_gates_passed"] is False
    assert res["environment_failure"] is not None
    assert "JVM failed during its own startup" in res["environment_failure"]
    assert mock_compile.call_count == 1
    assert res["failure_category"] == "environment_failure"
    trace_row = _latest_trace_row(cfg.paths.logs)
    assert trace_row is not None
    assert trace_row["status"] == "failure"
    assert trace_row["failure_category"] == "environment_failure"


@pytest.mark.asyncio
async def test_workflow_failure_category_none_on_success(tmp_path):
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write app.py",
        '[{"filepath": "app.py", "content": "print(1)"}]',
        "Review: Approved",
    ])
    we = WorkflowEngine(kernel, llm)
    res = await we.run_generation_workflow(goal="Create app", workspace_path=str(tmp_path))
    assert res["quality_gates_passed"] is True
    assert res["failure_category"] is None

@pytest.mark.asyncio
async def test_workflow_failure_category_quality_gates_exhausted(tmp_path):
    """An ordinary code failure that exhausts the retry budget - not an
    environment/toolchain failure - must be categorized distinctly, so a
    caller can always ask 'why did this fail' the same way regardless of
    which specific failure mode it was."""
    cfg = AppConfig()
    cfg.autonomy.run_verification_enabled = False
    cfg.paths.logs = str(tmp_path / "logs")
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=(
        ["Step 1: Write code", "Design: Write app.py"]
        + ["print('unterminated string"] * 15
        + ["Review: Approved"]
    ))
    we = WorkflowEngine(kernel, llm)
    res = await we.run_generation_workflow(goal="Create app", workspace_path=str(tmp_path))
    assert res["quality_gates_passed"] is False
    assert res["environment_failure"] is None
    assert res["failure_category"] == "quality_gates_exhausted"
    trace_row = _latest_trace_row(cfg.paths.logs)
    assert trace_row is not None
    assert trace_row["status"] == "failure"
    assert trace_row["failure_category"] == "quality_gates_exhausted"


@pytest.mark.asyncio
async def test_workflow_traces_knowledge_gap(tmp_path):
    """The knowledge-gap early return happens before the retry loop even starts,
    so it used to skip trace logging entirely - closing that gap so an eval
    harness reading traces.db can see this outcome, not just the ordinary
    success/failure path."""
    from kriya.tools.knowledge import GapReport

    cfg = AppConfig()
    cfg.paths.logs = str(tmp_path / "logs")
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    we = WorkflowEngine(kernel, llm)

    gap_report = GapReport()
    gap_report.add_gap("somelib", "9.9.9", None, "high", "released after training cutoff")

    with patch("kriya.tools.knowledge.KnowledgeGuard.check_goal", return_value=gap_report):
        res = await we.run_generation_workflow(goal="Use somelib 9.9.9", workspace_path=str(tmp_path))

    assert res["status"] == "knowledge_gap"
    trace_row = _latest_trace_row(cfg.paths.logs)
    assert trace_row is not None
    assert trace_row["status"] == "knowledge_gap"
    assert trace_row["failure_category"] == "knowledge_gap"


@pytest.mark.asyncio
async def test_workflow_retry_after_knowledge_gap_supersedes_the_transient_trace_row(tmp_path):
    """Regression test for a real live finding, 2026-08-07: kriya/cli.py's generate
    command calls run_generation_workflow() TWICE when a knowledge gap is
    auto-confirmed - once that hits the gap and returns immediately (writing the
    trace row asserted on above), then again with knowledge_risk_confirmed=True once
    the CLI decides to proceed. An eval-harness batch's own --timeout-per-goal killed
    that SECOND call (a genuine ~20-minute real run) before it ever wrote its own
    trace row, leaving only the harmless first row (status=knowledge_gap, <1s)
    behind - misleading, since the run's real outcome was never actually captured.

    trace_id_override lets the retry reuse the first call's own run_id - traces.db's
    run_id is the table's PRIMARY KEY and log_run() already does INSERT OR REPLACE,
    so passing it through (exactly as kriya/cli.py now does) means the retry's own
    eventual real outcome cleanly supersedes the transient knowledge_gap row instead
    of leaving two independent rows behind, with zero change to any other caller
    (trace_id_override defaults to None, preserving today's fresh-uuid-every-time
    behavior - confirmed by the unchanged test right above this one)."""
    from kriya.tools.knowledge import GapReport

    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.paths.logs = str(tmp_path / "logs")
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    we = WorkflowEngine(kernel, llm)

    gap_report = GapReport()
    gap_report.add_gap("somelib", "9.9.9", None, "high", "released after training cutoff")

    # Both calls patched under the SAME mock (a real generate -y run's second call
    # also re-runs KnowledgeGuard.check_goal() unconditionally - only
    # knowledge_risk_confirmed=True short-circuits the resulting gate, not the
    # check itself) - avoids the second call falling through to the REAL,
    # network-dependent check_goal() for a goal string ("somelib 9.9.9") that
    # would plausibly also read as a real gap.
    with patch("kriya.tools.knowledge.KnowledgeGuard.check_goal", return_value=gap_report):
        first_res = await we.run_generation_workflow(goal="Use somelib 9.9.9", workspace_path=str(tmp_path))
        assert first_res["status"] == "knowledge_gap"
        first_run_id = first_res["run_id"]

        llm.complete = AsyncMock(side_effect=[
            "Step 1: Write code",
            "Design: Write app.py",
            '[{"filepath": "app.py", "content": "print(1)"}]',
            "Review: Approved",
        ])
        second_res = await we.run_generation_workflow(
            goal="Use somelib 9.9.9", workspace_path=str(tmp_path),
            knowledge_risk_confirmed=True, trace_id_override=first_run_id,
        )
    assert second_res["quality_gates_passed"] is True

    db_path = os.path.join(cfg.paths.logs, "traces.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM runs").fetchall()
    conn.close()

    assert len(rows) == 1, [dict(r) for r in rows]  # NOT two independent rows
    assert rows[0]["run_id"] == first_run_id
    assert rows[0]["status"] == "success"  # the retry's real outcome, not "knowledge_gap"


@pytest.mark.asyncio
async def test_workflow_approval_required_but_no_callback_never_applies_changes(tmp_path):
    """Independent adversarial review, 2026-08-16, Finding 2: this gate used to fail
    OPEN - if policy required approval (human-in-the-loop mode here, but a sensitive-
    path match or an over-threshold diff size hit the exact same gap) but no
    approval_callback was ever wired, execution silently fell through to the
    unconditional apply-to-workspace step with no approval ever having been
    requested. Any direct run_generation_workflow() caller that doesn't supply a
    callback (a library/MCP integration, or a future CLI mode) got files copied into
    the real workspace completely unreviewed, in the one mode whose entire purpose
    is preventing exactly that. Must now refuse to apply and return a distinct,
    unambiguous signal instead."""
    cfg = AppConfig()
    cfg.autonomy.mode = "human-in-the-loop"
    cfg.autonomy.run_verification_enabled = False
    cfg.paths.logs = str(tmp_path / "logs")
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write app.py",
        '[{"filepath": "app.py", "content": "print(1)"}]',
        # No "Review:" response needed - the pre-approval Reviewer call is gated on
        # `approval_callback` too, so it never fires when one isn't wired either.
    ])

    we = WorkflowEngine(kernel, llm)
    res = await we.run_generation_workflow(
        goal="Create app",
        workspace_path=str(tmp_path),
        # Deliberately no approval_callback - the exact gap this test locks in.
    )

    assert res["quality_gates_passed"] is False
    assert res["files"] == []
    assert "approval was required" in res["review"]
    # The file must never have been written to the real workspace.
    assert not os.path.exists(os.path.join(str(tmp_path), "app.py"))
    trace_row = _latest_trace_row(cfg.paths.logs)
    assert trace_row is not None
    assert trace_row["status"] == "approval_required"
    assert trace_row["failure_category"] == "approval_required"

@pytest.mark.asyncio
async def test_workflow_traces_human_rejected(tmp_path):
    """The human-rejected-approval early return also used to skip trace logging
    entirely - same gap as the knowledge-gap path, closed the same way."""
    cfg = AppConfig()
    cfg.autonomy.mode = "human-in-the-loop"
    cfg.autonomy.run_verification_enabled = False
    cfg.paths.logs = str(tmp_path / "logs")
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write app.py",
        '[{"filepath": "app.py", "content": "print(1)"}]',
        # Stage 6 SME review, Finding 1: the Reviewer now runs at the Pre-Apply Human
        # Approval Gate itself (so its verdict can inform the decision) - BEFORE the
        # human's actual approve/reject answer, not after. Consumed here even though
        # this run ends in rejection.
        "Review: flagged for human judgment",
    ])

    we = WorkflowEngine(kernel, llm)
    res = await we.run_generation_workflow(
        goal="Create app",
        workspace_path=str(tmp_path),
        approval_callback=lambda files, reason: False,
    )

    assert res["quality_gates_passed"] is False
    assert res["review"] == "Rejected by user during approval gate review."
    trace_row = _latest_trace_row(cfg.paths.logs)
    assert trace_row is not None
    assert trace_row["status"] == "human_rejected"
    assert trace_row["failure_category"] == "human_rejected"


@pytest.mark.asyncio
async def test_workflow_reviewer_verdict_reaches_human_before_approval_decision(tmp_path):
    """Stage 6 SME review, Finding 1 (2026-08-15): before this fix, the Reviewer only
    ran AFTER the human-approval gate - a human approving in human-in-the-loop mode
    had no access to the Reviewer's opinion at all, since it didn't exist yet, and
    files were already applied to the real workspace before it ever ran. Confirms the
    real wiring: the Reviewer's text now reaches the approval callback's own `reason`
    argument - the actual thing a human sees at the decision point - not just that a
    Reviewer call happens somewhere in the run."""
    cfg = AppConfig()
    cfg.autonomy.mode = "human-in-the-loop"
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",             # Planner
        "Design: Write app.py",           # Architect
        '[{"filepath": "app.py", "content": "print(1)\\n"}]',  # Developer
        "REJECTED - hardcoded credential on line 4",  # Reviewer, at the approval gate
    ])

    captured_reasons = []
    def approval_cb(files, reason):
        captured_reasons.append(reason)
        return True

    we = WorkflowEngine(kernel, llm)
    res = await we.run_generation_workflow(
        goal="Create app", workspace_path=str(tmp_path), approval_callback=approval_cb,
    )

    assert res["quality_gates_passed"] is True
    assert len(captured_reasons) == 1
    assert "REJECTED - hardcoded credential on line 4" in captured_reasons[0]
    # Reused, not re-run: exactly 4 LLM calls total (no 5th for a redundant second
    # Reviewer call against the same final content), and the final result's own
    # "review" field is the SAME text the human already saw at approval time.
    assert llm.complete.await_count == 4
    assert res["review"] == "REJECTED - hardcoded credential on line 4"

@pytest.mark.asyncio
async def test_workflow_reviewer_not_run_early_when_no_approval_needed(tmp_path):
    """The pre-approval Reviewer call must only fire when a human is actually going
    to see it - autonomous mode (no escalation, no approval_callback needed) must pay
    zero extra latency/cost for it. Call count must match the pre-fix baseline exactly
    (Planner/Architect/Developer/Reviewer, one each) - not one more for a wasted early
    call nobody would ever see.

    Explicitly sets a non-"human-in-the-loop" mode - AutonomyConfig.mode defaults to
    "human-in-the-loop" (kriya/config/config.py), so a bare AppConfig() is NOT
    autonomous by default. This test originally omitted that override and was, until
    the Finding 2 fix (2026-08-16) closed the fail-open gap it was silently relying
    on, actually exercising "approval required but no callback provided, proceed
    anyway" rather than the "no approval needed" case its own name claimed."""
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write app.py",
        '[{"filepath": "app.py", "content": "print(1)\\n"}]',
        "Review: Approved",
    ])

    we = WorkflowEngine(kernel, llm)
    res = await we.run_generation_workflow(goal="Create app", workspace_path=str(tmp_path))

    assert res["quality_gates_passed"] is True
    assert llm.complete.await_count == 4
    assert res["review"] == "Review: Approved"

@pytest.mark.asyncio
async def test_workflow_stale_pre_approval_review_not_reused_after_later_attempt_fails(tmp_path):
    """Regression test for a real bug caught by independent review of Finding 1's own
    fix (2026-08-15): the original reset (`state.pre_approval_review = None`) was
    placed INSIDE the "4.5. Pre-Apply Human Approval Gate" section, so it only ran if
    a later loop iteration reached that section again. But run_attempt() raises
    QualityGateFailure directly from many sites (compile failure among them) that jump
    straight to the loop's `except` handler, skipping "4.5" entirely for that
    iteration - the ordinary, most common way an attempt fails. Reproduced exactly:
    attempt 1 reaches 4.5, gets approved, files copied - then the SEPARATE full
    regression-suite check fails, forcing a retry. Attempt 2's compile fails with an
    unrecoverable JVM-startup error (environment_failure - an IMMEDIATE loop break,
    never reaching 4.5 again). Without the fix (reset moved to run unconditionally,
    once per loop iteration, before run_attempt() is even called), the final "review"
    would still be attempt 1's stale, now-superseded verdict - describing content that
    no longer exists - instead of a fresh review of what actually, finally failed."""
    cfg = AppConfig()
    cfg.autonomy.mode = "human-in-the-loop"
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",                                     # Planner
        "Design: Write app.py",                                   # Architect
        '[{"filepath": "app.py", "content": "print(1)\\n"}]',     # Developer, attempt 1
        "Review A - stale, must not be reused",                   # Reviewer at 4.5, attempt 1
        '[{"filepath": "app.py", "content": "print(2)\\n"}]',     # Developer, attempt 2
        "Review B - fresh, describes the real final failure",     # Reviewer at "5.", after the loop ends
    ])

    jvm_error = (
        "Error occurred during initialization of VM\n"
        "java.lang.Error: A command line option has attempted to allow or "
        "enable the Security Manager. Enabling a Security Manager is not "
        "supported."
    )

    with patch(
        "kriya.tools.validate.PolymorphicValidator.run_compile_check",
        side_effect=[
            {"success": True, "output": ""},           # attempt 1: compiles fine
            {"success": False, "output": jvm_error},    # attempt 2: environment failure, immediate break
        ],
    ), patch(
        "kriya.tools.validate.PolymorphicValidator.run_tests",
        side_effect=[
            # workflow.py's own separate full-regression check, right after attempt 1's
            # approval + file-copy - fails, forcing the retry that leads to attempt 2.
            {"success": False, "output": "REGRESSION TEST SUITE FAILURE"},
        ],
    ):
        we = WorkflowEngine(kernel, llm)
        res = await we.run_generation_workflow(
            goal="Create app", workspace_path=str(tmp_path),
            approval_callback=lambda files, reason: True,
        )

    assert res["quality_gates_passed"] is False
    assert res["environment_failure"] is not None
    assert res["review"] == "Review B - fresh, describes the real final failure"
    assert llm.complete.await_count == 6


@pytest.mark.asyncio
async def test_workflow_surfaces_toolchain_warning_even_on_success(tmp_path):
    """Toolchain preflight: a java/mvn JDK version mismatch is a real, silent
    risk even on a run that happens to succeed anyway (a different goal, or a
    different machine, could still hit the same crash later) - so it must be
    surfaced regardless of the run's own pass/fail outcome, not folded only
    into the environment_failure circuit breaker's failure-only reporting."""
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write pom.xml",
        # A bare string, not JSON - see test_workflow_full_set_prompt_
        # includes_existing_dependencies_checklist's own comment above for why
        # (known_target_files bypasses Step 1's parse_file_list() entirely for
        # this single-file case, so this response is used directly as raw
        # file content, not JSON to be parsed).
        "<project></project>",
        "Review: Approved",
    ])

    we = WorkflowEngine(kernel, llm)

    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check") as mock_compile, \
         patch("kriya.tools.validate.PolymorphicValidator.run_pom_validate", return_value={"success": True, "output": ""}), \
         patch("kriya.tools.validate.PolymorphicValidator.run_tests", return_value={"success": True, "output": ""}), \
         patch("kriya.tools.validate.check_java_toolchain", return_value={
             "java_found": True, "java_version": "17",
             "mvn_found": True, "mvn_java_version": "26",
             "mismatch": True,
         }) as mock_toolchain:
        mock_compile.return_value = {"success": True, "output": "Maven compilation succeeded."}

        res = await we.run_generation_workflow(
            # Goal text must itself evidence Java - _java_toolchain_fact() is
            # now gated by _goal_or_repo_targets_java() (2026-08-06 fix), so a
            # stack-neutral goal like "Create pom" would never reach the
            # toolchain check at all before this test's real target behavior
            # (the mismatch check, once stack resolves to java) gets a chance
            # to run.
            goal="Create a Java pom",
            workspace_path=str(tmp_path)
        )

    assert res["quality_gates_passed"] is True
    assert res["toolchain_warning"] is not None
    assert "JDK 17" in res["toolchain_warning"] and "JDK 26" in res["toolchain_warning"]
    # Called three times total, still just once per generation run (not once
    # per retry attempt, the property this test actually guards): once early
    # for the "Target JVM" fact (goal text evidences Java, so
    # _goal_or_repo_targets_java() allows it even before stack is confirmed
    # from real files), once more from the retry loop's own mismatch check
    # once stack resolves to java, and once more from
    # _resolve_java_home_override() (2026-08-07) checking whether this same
    # mismatch is also something the goal cares about correcting - this
    # goal's text ("Create a Java pom") names no specific version, so that
    # third call returns None without needing anything beyond the mismatch
    # flag already fetched.
    assert mock_toolchain.call_count == 3


@pytest.mark.asyncio
async def test_workflow_checks_toolchain_only_once_across_retries(tmp_path):
    """The toolchain preflight check runs a real local subprocess pair
    (java -version, mvn -version) - must not repeat it on every retry attempt,
    only once per generation run, regardless of which of the two
    PolymorphicValidator construction sites reaches it first."""
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write pom.xml",
        # Attempt 1's response (bare string, not JSON): known_target_files
        # (from attempt 1's heuristic-extracted expected_files_upfront)
        # bypasses Step 1's parse_file_list() entirely for this single-file
        # case - see test_workflow_full_set_prompt_includes_existing_
        # dependencies_checklist's own comment above for why. Deliberately
        # well-formed XML (unlike an actually-malformed
        # "<project><bad></project>", which find_structural_corruption's own
        # XML check would catch BEFORE ever reaching the mocked compile gate
        # below, consuming an extra, unbudgeted retry cycle) - this test wants
        # attempt 1 to reach and fail AT the (fully mocked, content-agnostic)
        # compile gate specifically, matching mock_compile.side_effect's own
        # two entries below.
        "<project><bad/></project>",
        # Attempt 2's response (back to the JSON-array shape): the mocked
        # compile failure text ("some generic xml error") names no real file,
        # so extract_implicated_files() can't scope this retry to pom.xml -
        # it falls through to an UNSCOPED full-set retry, which genuinely
        # does go through Step 1's batch-JSON-with-content path (unlike
        # attempt 1's known_target_files-scoped call above) - confirmed via a
        # standalone repro before landing on this content.
        '[{"filepath": "pom.xml", "content": "<project></project>"}]',
        "Review: Approved",
    ])

    we = WorkflowEngine(kernel, llm)

    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check") as mock_compile, \
         patch("kriya.tools.validate.PolymorphicValidator.run_pom_validate", return_value={"success": True, "output": ""}), \
         patch("kriya.tools.validate.PolymorphicValidator.run_tests", return_value={"success": True, "output": ""}), \
         patch("kriya.tools.validate.check_java_toolchain", return_value={
             "java_found": True, "java_version": "17",
             "mvn_found": True, "mvn_java_version": "17",
             "mismatch": False,
         }) as mock_toolchain:
        mock_compile.side_effect = [
            {"success": False, "output": "COMPILATION FAILURE:\nsome generic xml error"},
            {"success": True, "output": "Maven compilation succeeded."},
        ]

        res = await we.run_generation_workflow(
            # Goal text must evidence Java - see the comment in
            # test_workflow_surfaces_toolchain_warning_even_on_success above.
            goal="Create a Java pom",
            workspace_path=str(tmp_path)
        )

    assert res["quality_gates_passed"] is True
    # Called three times total (fact + mismatch-check + _resolve_java_home_
    # override()'s own check, added 2026-08-07 - this run has no mismatch at
    # all, so that third call returns None immediately), not once per retry
    # attempt - the "only once across retries" property this test is really
    # guarding.
    assert mock_toolchain.call_count == 3


@pytest.mark.asyncio
async def test_workflow_skips_toolchain_check_for_non_java_stack(tmp_path):
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write app.py",
        '[{"filepath": "app.py", "content": "print(1)"}]',
        "Review: Approved",
    ])

    we = WorkflowEngine(kernel, llm)

    # check_java_toolchain() must NOT be called at all: _java_toolchain_fact()
    # is gated by _goal_or_repo_targets_java() (2026-08-06 fix) and this
    # goal's text has no Java/Maven/Gradle/Spring/JVM evidence, so the early
    # fact-lookup is skipped outright - and once the retry loop confirms this
    # run's actual stack is python, not java, the mismatch check is skipped
    # too. A real subprocess pair (java -version, mvn -version) is now never
    # spent on a goal that was never going to be Java, closing a real,
    # previously-unconditional cost this test used to accept as normal.
    with patch("kriya.tools.validate.check_java_toolchain", return_value={
        "java_found": False, "java_version": None,
        "mvn_found": False, "mvn_java_version": None,
        "mismatch": False,
    }) as mock_toolchain:
        res = await we.run_generation_workflow(
            goal="Create a Python app",
            workspace_path=str(tmp_path)
        )

    assert res["quality_gates_passed"] is True
    assert res["toolchain_warning"] is None
    assert mock_toolchain.call_count == 0


@pytest.mark.asyncio
async def test_workflow_injects_target_jvm_fact_into_planner_prompt(tmp_path):
    """A JDK-version-conditional skill rule (e.g. a JVM startup flag required on
    one JDK range, fatal on another) is unverifiable at generation time unless
    the model has a concrete resolved JDK number to reason against - the fact
    must reach the Planner prompt itself, not just be logged. Goal text must
    itself evidence Java (_goal_or_repo_targets_java(), 2026-08-06 fix) for
    the fact to be looked up at all - a stack-neutral goal no longer gets it
    just because Java happens to be installed on the machine running this.
    Kept the generated file itself as plain Python (real, unmocked compile
    via Python's own compile() builtin, no external tool dependency) -
    _java_toolchain_fact() is gated purely on goal text/workspace markers,
    resolved before any file exists, so what actually gets generated
    afterward doesn't matter for what this test is checking."""
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write app.py",
        '[{"filepath": "app.py", "content": "print(1)"}]',
        "Review: Approved",
    ])

    we = WorkflowEngine(kernel, llm)

    with patch("kriya.tools.validate.check_java_toolchain", return_value={
        "java_found": True, "java_version": "17",
        "mvn_found": True, "mvn_java_version": "26",
        "mismatch": True,
    }):
        res = await we.run_generation_workflow(
            goal="Create a Java app",
            workspace_path=str(tmp_path)
        )

    assert res["quality_gates_passed"] is True
    planner_prompt = llm.complete.call_args_list[0].args[1]
    assert "Target JVM" in planner_prompt
    assert "JDK 26" in planner_prompt


@pytest.mark.asyncio
async def test_workflow_omits_target_jvm_fact_when_no_java_toolchain(tmp_path):
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write app.py",
        '[{"filepath": "app.py", "content": "print(1)"}]',
        "Review: Approved",
    ])

    we = WorkflowEngine(kernel, llm)

    with patch("kriya.tools.validate.check_java_toolchain", return_value={
        "java_found": False, "java_version": None,
        "mvn_found": False, "mvn_java_version": None,
        "mismatch": False,
    }):
        res = await we.run_generation_workflow(
            goal="Create app",
            workspace_path=str(tmp_path)
        )

    assert res["quality_gates_passed"] is True
    planner_prompt = llm.complete.call_args_list[0].args[1]
    assert "Target JVM" not in planner_prompt


@pytest.mark.asyncio
async def test_workflow_run_verification_goal_explicit_passes_without_confirmation(tmp_path):
    """A goal-text-explicit run command is pre-authorized by the user and must proceed
    without needing the human-in-the-loop confirmation gate, even though that's the
    default autonomy mode."""
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    # design's "app.py" mention activates known_target_files on attempt 1, skipping
    # straight to a plain per-file content completion.
    file_content_response = "print('[SUCCESS] it worked')\n"
    judge_response = json.dumps({
        "should_run": True,
        "run_commands": [[sys.executable, "app.py"]],
        "command_source": "goal_explicit",
        "success_criteria": "Output contains [SUCCESS]"
    })
    grade_response = json.dumps({"passed": True, "reasoning": "Output contains the expected [SUCCESS] line."})

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",     # Planner
        "Design: Write app.py",   # Architect
        file_content_response,    # Developer
        judge_response,           # RunVerifier.judge
        grade_response,           # RunVerifier.grade
        "Review: Approved"        # Reviewer
    ])

    we = WorkflowEngine(kernel, llm)

    res = await we.run_generation_workflow(
        goal="Run with python app.py; it should print [SUCCESS]",
        workspace_path=str(tmp_path)
    )

    assert res["quality_gates_passed"] is True
    assert "app.py" in res["files"]

def test_extract_contract_verdict_returns_none_without_marker():
    """No marker present must fall through to today's LLM-graded behavior
    unchanged - the whole point of this being a soft, optional convention."""
    assert extract_contract_verdict("some build output\nno marker here") is None

def test_extract_contract_verdict_single_pass():
    result = extract_contract_verdict("Starting app...\n[VERIFICATION] PASS\nDone.")
    assert result["passed"] is True
    assert result["likely_files"] == []

def test_extract_contract_verdict_single_fail_carries_reason():
    result = extract_contract_verdict("[VERIFICATION] FAIL: decoded string did not match original")
    assert result["passed"] is False
    assert "decoded string did not match original" in result["reasoning"]

def test_extract_contract_verdict_multiple_pass_all_required():
    """A goal that runs multiple sequential commands (e.g. "add a task, then
    list it") can legitimately print one verdict line per step - all must
    pass for the overall contract to pass."""
    result = extract_contract_verdict("[VERIFICATION] PASS\nsome noise\n[VERIFICATION] PASS")
    assert result["passed"] is True

def test_extract_contract_verdict_any_fail_fails_whole_sequence():
    result = extract_contract_verdict("[VERIFICATION] PASS\n[VERIFICATION] FAIL: task not found in list")
    assert result["passed"] is False
    assert "task not found in list" in result["reasoning"]

def test_extract_contract_verdict_ignores_marker_not_at_line_start():
    """Only a real standalone verdict line counts - text that merely mentions
    the marker mid-line (e.g. echoed goal text, a log message quoting it)
    must not be mistaken for an actual verdict."""
    assert extract_contract_verdict("noise [VERIFICATION] PASS trailing junk") is None

def test_pass_verdict_is_grounded_true_when_fail_string_present():
    """Independent brutal review finding #4: a genuine implementation, per
    VERIFICATION_CONTRACT_HEADER's own instruction, has to literally write
    the "[VERIFICATION] FAIL" string somewhere in source for that branch to
    exist at all - however it's structured (if/else, ternary, whatever)."""
    files = [
        "public class App {\n"
        "    public static void main(String[] args) {\n"
        "        if (decoded.equals(original)) {\n"
        '            System.out.println("[VERIFICATION] PASS");\n'
        "        } else {\n"
        '            System.out.println("[VERIFICATION] FAIL: mismatch");\n'
        "        }\n"
        "    }\n"
        "}\n"
    ]
    assert pass_verdict_is_grounded(files) is True

def test_pass_verdict_is_grounded_false_when_no_fail_string_anywhere():
    """The actual gap this closes: a file that only ever prints PASS, with
    no FAIL branch anywhere - the "check" doesn't look like it branches on
    anything, so the PASS marker shouldn't be blindly trusted."""
    files = [
        "public class App {\n"
        "    public static void main(String[] args) {\n"
        '        System.out.println("[VERIFICATION] PASS");\n'
        "    }\n"
        "}\n"
    ]
    assert pass_verdict_is_grounded(files) is False

def test_pass_verdict_is_grounded_checks_across_all_files_not_just_one():
    """The FAIL-handling code doesn't have to live in the same file as the
    PASS print - checking across the whole written-files set, not just one
    file in isolation, matters for a multi-file batch."""
    files = [
        'System.out.println("[VERIFICATION] PASS");\n',
        'System.out.println("[VERIFICATION] FAIL: from a helper class");\n',
    ]
    assert pass_verdict_is_grounded(files) is True

def test_pass_verdict_is_grounded_empty_input():
    assert pass_verdict_is_grounded([]) is False

@pytest.mark.asyncio
async def test_workflow_verification_contract_marker_skips_llm_grade_on_pass(tmp_path):
    """When the generated entrypoint prints the deterministic verification-contract
    marker (see VERIFICATION_CONTRACT_HEADER / extract_contract_verdict), Runtime
    Verification must trust it directly and never call RunVerifierAgent.grade() at
    all. Added 2026-08-11 after grade() twice independently hallucinated a wrong
    "expected" value and rejected genuinely correct code even though the program's
    own real comparison had already passed and printed so - see
    VERIFICATION_CONTRACT_HEADER's rationale comment in
    kriya/workflow/retry_prompts.py for the full incident."""
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",     # Planner
        "Design: Write app.py",   # Architect
        "Review: Approved",       # Reviewer
    ])
    we = WorkflowEngine(kernel, llm)
    we.developer.run_generation = AsyncMock(return_value=[
        {"filepath": "app.py", "content": (
            "print('hi')\n"
            "if True:\n"
            "    print('[VERIFICATION] PASS')\n"
            "else:\n"
            "    print('[VERIFICATION] FAIL: reason')\n"
        )}
    ])
    we.run_verifier.judge = AsyncMock(return_value={
        "should_run": True,
        "run_commands": [[sys.executable, "app.py"]],
        "command_source": "goal_explicit",
        "success_criteria": "Prints a [VERIFICATION] verdict line",
    })
    we.run_verifier.grade = AsyncMock(
        side_effect=AssertionError("grade() must not be called when the verification-contract marker is present")
    )

    with patch(
        "kriya.tools.validate.PolymorphicValidator.run_compile_check",
        return_value={"success": True, "output": ""},
    ), patch(
        "kriya.tools.validate.PolymorphicValidator.run_tests",
        return_value={"success": True, "output": ""},
    ), patch(
        "kriya.tools.validate.PolymorphicValidator.run_app_sequence",
        return_value={"success": True, "timed_out": False, "returncode": 0, "output": "hi\n[VERIFICATION] PASS"},
    ):
        res = await we.run_generation_workflow(
            goal="Run with python app.py; it should self-verify",
            workspace_path=str(tmp_path),
        )

    we.run_verifier.grade.assert_not_called()
    assert res["quality_gates_passed"] is True

@pytest.mark.asyncio
async def test_workflow_ungrounded_pass_marker_falls_back_to_llm_grade(tmp_path):
    """Independent brutal review finding #4, end-to-end: a PASS marker whose
    written file contains no "[VERIFICATION] FAIL" string anywhere must NOT
    be blindly trusted - confirms grade() genuinely gets called (the real
    wiring through _extract_grounded_contract_verdict() in attempt.py), not
    just the pure pass_verdict_is_grounded() function in isolation."""
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write app.py",
        "Review: Approved",
    ])
    we = WorkflowEngine(kernel, llm)
    # No FAIL branch anywhere in this file - the actual gap finding #4 closes.
    we.developer.run_generation = AsyncMock(return_value=[
        {"filepath": "app.py", "content": "print('hi')\nprint('[VERIFICATION] PASS')\n"}
    ])
    we.run_verifier.judge = AsyncMock(return_value={
        "should_run": True,
        "run_commands": [[sys.executable, "app.py"]],
        "command_source": "goal_explicit",
        "success_criteria": "Prints a [VERIFICATION] verdict line",
    })
    we.run_verifier.grade = AsyncMock(return_value={"passed": True, "reasoning": "LLM grader agreed"})

    with patch(
        "kriya.tools.validate.PolymorphicValidator.run_compile_check",
        return_value={"success": True, "output": ""},
    ), patch(
        "kriya.tools.validate.PolymorphicValidator.run_tests",
        return_value={"success": True, "output": ""},
    ), patch(
        "kriya.tools.validate.PolymorphicValidator.run_app_sequence",
        return_value={"success": True, "timed_out": False, "returncode": 0, "output": "hi\n[VERIFICATION] PASS"},
    ):
        res = await we.run_generation_workflow(
            goal="Run with python app.py; it should self-verify",
            workspace_path=str(tmp_path),
        )

    we.run_verifier.grade.assert_called_once()
    assert res["quality_gates_passed"] is True

@pytest.mark.asyncio
async def test_workflow_run_verification_gate_outcome_records_graded_by_contract(tmp_path):
    """Companion to the marker-skips-llm-grade test above: the persisted
    gate_outcome must record HOW a run_verification pass was decided, not just
    that it passed - added so a future eval-harness batch can query
    graded_by's distribution across many runs directly from traces.db instead
    of grepping raw stdout logs by hand for "[VERIFICATION]", which is what
    diagnosing the underlying grader-reliability gap required this session."""
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.paths.logs = str(tmp_path / "logs")
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write app.py",
        "Review: Approved",
    ])
    we = WorkflowEngine(kernel, llm)
    we.developer.run_generation = AsyncMock(return_value=[
        {"filepath": "app.py", "content": (
            "print('hi')\n"
            "if True:\n"
            "    print('[VERIFICATION] PASS')\n"
            "else:\n"
            "    print('[VERIFICATION] FAIL: reason')\n"
        )}
    ])
    we.run_verifier.judge = AsyncMock(return_value={
        "should_run": True,
        "run_commands": [[sys.executable, "app.py"]],
        "command_source": "goal_explicit",
        "success_criteria": "Prints a [VERIFICATION] verdict line",
    })
    we.run_verifier.grade = AsyncMock(
        side_effect=AssertionError("grade() must not be called when the verification-contract marker is present")
    )

    with patch(
        "kriya.tools.validate.PolymorphicValidator.run_compile_check",
        return_value={"success": True, "output": ""},
    ), patch(
        "kriya.tools.validate.PolymorphicValidator.run_tests",
        return_value={"success": True, "output": ""},
    ), patch(
        "kriya.tools.validate.PolymorphicValidator.run_app_sequence",
        return_value={"success": True, "timed_out": False, "returncode": 0, "output": "hi\n[VERIFICATION] PASS"},
    ):
        res = await we.run_generation_workflow(
            goal="Run with python app.py; it should self-verify",
            workspace_path=str(tmp_path),
        )

    assert res["quality_gates_passed"] is True
    db_path = os.path.join(cfg.paths.logs, "traces.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM runs ORDER BY timestamp DESC LIMIT 1").fetchone()
    conn.close()
    gate_outcomes = json.loads(row["gate_outcomes"])
    rv_outcome = next(g for g in gate_outcomes if g["type"] == "run_verification")
    assert rv_outcome["graded_by"] == "contract"

@pytest.mark.asyncio
async def test_workflow_run_verification_gate_outcome_records_graded_by_llm(tmp_path):
    """Sibling to the contract test above: when no marker is present, grade()
    IS called and the gate_outcome must record graded_by="llm" - confirms the
    field reflects which path actually decided the outcome, not a hardcoded
    value."""
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.paths.logs = str(tmp_path / "logs")
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write app.py",
        "Review: Approved",
    ])
    we = WorkflowEngine(kernel, llm)
    we.developer.run_generation = AsyncMock(return_value=[
        {"filepath": "app.py", "content": "print('hi')\n"}
    ])
    we.run_verifier.judge = AsyncMock(return_value={
        "should_run": True,
        "run_commands": [[sys.executable, "app.py"]],
        "command_source": "goal_explicit",
        "success_criteria": "Prints hi",
    })
    we.run_verifier.grade = AsyncMock(return_value={"passed": True, "reasoning": "Printed hi as expected."})

    with patch(
        "kriya.tools.validate.PolymorphicValidator.run_compile_check",
        return_value={"success": True, "output": ""},
    ), patch(
        "kriya.tools.validate.PolymorphicValidator.run_tests",
        return_value={"success": True, "output": ""},
    ), patch(
        "kriya.tools.validate.PolymorphicValidator.run_app_sequence",
        return_value={"success": True, "timed_out": False, "returncode": 0, "output": "hi\n"},
    ):
        res = await we.run_generation_workflow(
            goal="Run with python app.py; it should print hi",
            workspace_path=str(tmp_path),
        )

    assert res["quality_gates_passed"] is True
    we.run_verifier.grade.assert_called_once()
    db_path = os.path.join(cfg.paths.logs, "traces.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM runs ORDER BY timestamp DESC LIMIT 1").fetchone()
    conn.close()
    gate_outcomes = json.loads(row["gate_outcomes"])
    rv_outcome = next(g for g in gate_outcomes if g["type"] == "run_verification")
    assert rv_outcome["graded_by"] == "llm"

@pytest.mark.asyncio
async def test_workflow_verification_contract_marker_skips_llm_grade_on_fail(tmp_path):
    """Same deterministic short-circuit for a FAIL marker: the run_verification gate
    must fail using the marker's own reason text, still without ever invoking
    grade() - the contract check runs on every retry attempt, not just the first."""
    cfg = AppConfig()
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",     # Planner
        "Design: Write app.py",   # Architect
        "Review: Approved",       # Reviewer - still runs after retries are exhausted
    ])
    we = WorkflowEngine(kernel, llm)
    we.developer.run_generation = AsyncMock(return_value=[
        {"filepath": "app.py", "content": (
            "print('hi')\n"
            "if True:\n"
            "    print('[VERIFICATION] PASS')\n"
            "else:\n"
            "    print('[VERIFICATION] FAIL: reason')\n"
        )}
    ])
    we.run_verifier.judge = AsyncMock(return_value={
        "should_run": True,
        "run_commands": [[sys.executable, "app.py"]],
        "command_source": "goal_explicit",
        "success_criteria": "Prints a [VERIFICATION] verdict line",
    })
    we.run_verifier.grade = AsyncMock(
        side_effect=AssertionError("grade() must not be called when the verification-contract marker is present")
    )

    with patch(
        "kriya.tools.validate.PolymorphicValidator.run_compile_check",
        return_value={"success": True, "output": ""},
    ), patch(
        "kriya.tools.validate.PolymorphicValidator.run_tests",
        return_value={"success": True, "output": ""},
    ), patch(
        "kriya.tools.validate.PolymorphicValidator.run_app_sequence",
        return_value={
            "success": True, "timed_out": False, "returncode": 0,
            "output": "hi\n[VERIFICATION] FAIL: decoded value did not match original",
        },
    ):
        res = await we.run_generation_workflow(
            goal="Run with python app.py; it should self-verify",
            workspace_path=str(tmp_path),
        )

    we.run_verifier.grade.assert_not_called()
    assert res["quality_gates_passed"] is False

@pytest.mark.asyncio
async def test_workflow_verification_contract_marker_used_on_plain_nonzero_exit(tmp_path):
    """Independent brutal review finding #1 (2026-08-15): the plain
    "run_app_sequence succeeded=False, no timeout" branch in attempt.py used
    to short-circuit straight to a synthetic "one or more steps failed"
    message, never checking extract_contract_verdict() or calling grade() at
    all - the only one of the three outcome branches that skipped both. A
    real crash (a step exits nonzero, no hang) is plausibly the single most
    common real failure shape, and exactly the case an entrypoint that
    detects its own failure and exits nonzero after printing
    "[VERIFICATION] FAIL: <reason>" would hit - that rich, deterministic
    diagnosis was being silently discarded for a generic message, and
    likely_files was always empty, giving the retry loop zero file-
    attribution signal for this failure class. Confirms the marker's own
    reason text now reaches the persisted gate outcome and grade() is never
    called, mirroring the sibling test above for the clean-exit-with-FAIL-
    marker case."""
    cfg = AppConfig()
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",     # Planner
        "Design: Write app.py",   # Architect
        "Review: Approved",       # Reviewer - still runs after retries are exhausted
    ])
    we = WorkflowEngine(kernel, llm)
    we.developer.run_generation = AsyncMock(return_value=[
        {"filepath": "app.py", "content": (
            "print('hi')\n"
            "if True:\n"
            "    print('[VERIFICATION] PASS')\n"
            "else:\n"
            "    print('[VERIFICATION] FAIL: reason')\n"
        )}
    ])
    we.run_verifier.judge = AsyncMock(return_value={
        "should_run": True,
        "run_commands": [[sys.executable, "app.py"]],
        "command_source": "goal_explicit",
        "success_criteria": "Prints a [VERIFICATION] verdict line",
    })
    we.run_verifier.grade = AsyncMock(
        side_effect=AssertionError("grade() must not be called when the verification-contract marker is present")
    )

    with patch(
        "kriya.tools.validate.PolymorphicValidator.run_compile_check",
        return_value={"success": True, "output": ""},
    ), patch(
        "kriya.tools.validate.PolymorphicValidator.run_tests",
        return_value={"success": True, "output": ""},
    ), patch(
        "kriya.tools.validate.PolymorphicValidator.run_app_sequence",
        return_value={
            "success": False, "timed_out": False, "returncode": 1,
            "output": "hi\n[VERIFICATION] FAIL: decoded value did not match original",
        },
    ):
        res = await we.run_generation_workflow(
            goal="Run with python app.py; it should self-verify",
            workspace_path=str(tmp_path),
        )

    we.run_verifier.grade.assert_not_called()
    assert res["quality_gates_passed"] is False

def _minimal_attempt_ctx(tmp_path, **overrides) -> AttemptContext:
    """Builds an AttemptContext with sensible fake/mocked defaults for testing
    run_attempt() in true isolation - no WorkflowEngine, no Planner/Architect/
    Graph RAG, no worktree. This is the whole point of Opportunity 2 Slice 2:
    a targeted fix to Quality Gates logic no longer needs the full pipeline's
    mock chain to write a test against."""
    defaults = dict(
        goal="Write a small app",
        plan="Step 1: write it",
        design="Design: one file",
        workspace_path=str(tmp_path),
        worktree_path=str(tmp_path),
        architect_files=["app.py"],
        resume_state=None,
        run_id="test-run-id",
        skills_prompt="",
        learned_rag_context="",
        matched_files=[],
        related_files=[],
        ecosystem_invariant_block="",
        resource_lifecycle_block="",
        verification_contract_block="",
        required_files_prompt_block="",
        required_dependencies_prompt_block="",
        expected_files_upfront=["app.py"],
        architect_basename_to_path={"app.py": "app.py"},
        chain=[],
        targeted_max_retries=3,
        stream_callback=None,
        approval_callback=None,
        active_skills=[],
        active_skill_rules_snapshot={},
        developer=AsyncMock(),
        run_verifier=AsyncMock(),
        skill_engine=MagicMock(),
        kernel=Kernel(config=AppConfig()),
        max_retries=4,
        web_lookup_query_callback=None,
        approve_web_lookup=AsyncMock(return_value=False),
    )
    defaults.update(overrides)
    return AttemptContext(**defaults)

@pytest.mark.asyncio
async def test_run_attempt_isolated_compile_failure_raises_quality_gate_failure(tmp_path):
    """The actual payoff of Slice 2: a targeted fix to Quality Gates logic can
    now be tested by calling run_attempt() directly with a hand-built
    GenerationState/AttemptContext and a mocked Developer/validator - no full
    WorkflowEngine, no Planner/Architect/Graph RAG mocks, no worktree setup."""
    state = GenerationState()
    developer = AsyncMock()
    developer.run_generation = AsyncMock(return_value=[
        {"filepath": "app.py", "content": "this is not valid python("}
    ])
    ctx = _minimal_attempt_ctx(tmp_path, developer=developer)

    with patch(
        "kriya.tools.validate.PolymorphicValidator.run_compile_check",
        return_value={"success": False, "output": "SyntaxError: invalid syntax"},
    ):
        with pytest.raises(QualityGateFailure) as exc_info:
            await run_attempt(state, ctx)

    assert exc_info.value.failure.type == "compile"
    assert "app.py" in state.all_files_written
    assert state.gate_outcomes[-1]["type"] == "compile"
    assert state.gate_outcomes[-1]["success"] is False

@pytest.mark.asyncio
async def test_run_attempt_still_requests_approval_when_goal_explicit_claim_is_ungrounded(tmp_path):
    """Independent brutal review finding #2, end-to-end: RunVerifierAgent.judge()
    self-reporting command_source="goal_explicit" for a command whose
    executable never appears in the goal text must NOT bypass the human-in-
    the-loop approval gate - confirms the real wiring (downgrade_ungrounded_
    goal_explicit_commands() called from attempt.py, before caching, before
    the approval-gate check), not just the pure function in isolation."""
    state = GenerationState()
    developer = AsyncMock()
    developer.run_generation = AsyncMock(return_value=[{"filepath": "app.py", "content": "print('hi')\n"}])
    run_verifier = AsyncMock()
    run_verifier.judge = AsyncMock(return_value={
        "should_run": True,
        # "curl" appears nowhere in the goal text below - a mislabeled claim.
        "run_commands": [["curl", "http://internal-service/admin"]],
        "command_source": "goal_explicit",
        "success_criteria": "returns 200",
    })
    approval_callback = MagicMock(return_value=True)

    cfg = AppConfig()
    cfg.autonomy.mode = "human-in-the-loop"
    ctx = _minimal_attempt_ctx(
        tmp_path, developer=developer, run_verifier=run_verifier,
        approval_callback=approval_callback, kernel=Kernel(config=cfg),
        architect_files=["app.py"], expected_files_upfront=["app.py"],
    )

    with patch(
        "kriya.tools.validate.PolymorphicValidator.run_compile_check",
        return_value={"success": True, "output": ""},
    ), patch(
        "kriya.tools.validate.PolymorphicValidator.run_app_sequence",
        return_value={"success": True, "timed_out": False, "returncode": 0, "output": "hi\n"},
    ):
        await run_attempt(state, ctx)

    approval_callback.assert_called_once()
    confirm_reason = approval_callback.call_args[0][1]
    assert "curl" in confirm_reason

@pytest.mark.asyncio
async def test_run_attempt_static_check_fires_before_compile_gate(tmp_path):
    """Regression test for the static pre-check added 2026-08-12 (evidenced by
    the SAME live ignite_qpid_protocol run's "already been started" failure):
    a generated app mixing Apache Ignite's Method A (Ignition.start() direct)
    with Method B (an XML defining IgniteSpringBean) must be caught by
    kriya/workflow/static_checks.py BEFORE the expensive compile gate ever
    runs - PolymorphicValidator.run_compile_check must never be called."""
    state = GenerationState()
    developer = AsyncMock()
    developer.run_generation = AsyncMock(return_value=[
        {"filepath": "ProtocolApp.java", "content": (
            'public class ProtocolApp {\n'
            '    public static void main(String[] args) throws Exception {\n'
            '        Ignite ignite = Ignition.start("ignite-config.xml");\n'
            '    }\n'
            '}\n'
        )},
        {"filepath": "ignite-config.xml", "content": (
            '<beans>\n'
            '    <bean id="igniteNode" class="org.apache.ignite.IgniteSpringBean">\n'
            '        <property name="configuration" ref="ignite.cfg"/>\n'
            '    </bean>\n'
            '</beans>\n'
        )},
    ])
    ctx = _minimal_attempt_ctx(
        tmp_path, developer=developer,
        architect_files=["ProtocolApp.java", "ignite-config.xml"],
        expected_files_upfront=["ProtocolApp.java", "ignite-config.xml"],
        architect_basename_to_path={"ProtocolApp.java": "ProtocolApp.java", "ignite-config.xml": "ignite-config.xml"},
    )

    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check") as mock_compile:
        with pytest.raises(QualityGateFailure) as exc_info:
            await run_attempt(state, ctx)

    mock_compile.assert_not_called()
    assert exc_info.value.failure.type == "static_rule_violation"
    assert "ignite_method_mixing" in exc_info.value.failure.message
    assert state.gate_outcomes[-1]["type"] == "static_rule_violation"
    # Both files are genuinely implicated by this specific check (Method A in
    # one, Method B in the other) - both belong in likely_files, unlike the
    # over-broadcast regression test below where only one of several written
    # files is actually implicated.
    assert set(exc_info.value.failure.likely_files) == {"ProtocolApp.java", "ignite-config.xml"}

@pytest.mark.asyncio
async def test_run_attempt_cleans_up_runtime_artifacts_between_attempts(tmp_path):
    """Regression test for a real bug found live via the corpus survey's dig
    into run_verification (python_task_tracker, runs b-6/b-7, 2026-08-16, see
    clean_untracked_files_since's own docstring in kriya/workflow/worktree.py
    for the full incident): the SAME worktree is reused across retry attempts
    (compile caches), but nothing cleaned up a runtime state file (a JSON
    store) a PRIOR attempt's own verification run wrote to disk - so a later
    attempt's run started from stale leftover state instead of a fresh one,
    producing failures that had nothing to do with the generated code.

    Calls run_attempt() twice against the SAME state/worktree, with a
    run_app_sequence() side effect that mimics a stateful app: it writes
    'tasks.json' with this call's own attempt number, and records whether
    the file already existed (with a DIFFERENT attempt's content) before it
    ran. Pre-fix, the second call would see the first attempt's leftover
    file; post-fix, each attempt starts clean."""
    _init_git_repo(tmp_path)
    state = GenerationState()
    developer = AsyncMock()
    developer.run_generation = AsyncMock(return_value=[{"filepath": "app.py", "content": "print('hi')\n"}])
    run_verifier = AsyncMock()
    run_verifier.judge = AsyncMock(return_value={
        "should_run": True,
        "run_commands": [[sys.executable, "app.py"]],
        "command_source": "goal_explicit",
        "success_criteria": "Prints a [VERIFICATION] verdict line",
    })
    ctx = _minimal_attempt_ctx(tmp_path, developer=developer, run_verifier=run_verifier)

    tasks_json_path = os.path.join(str(tmp_path), "tasks.json")
    pre_call_leftover_content = []

    def fake_run_app_sequence(commands, timeout=90):
        if os.path.exists(tasks_json_path):
            with open(tasks_json_path) as f:
                pre_call_leftover_content.append(f.read())
        else:
            pre_call_leftover_content.append(None)
        with open(tasks_json_path, "w") as f:
            f.write(f"attempt-{state.attempt_number}")
        return {"success": True, "timed_out": False, "returncode": 0, "output": "hi\n[VERIFICATION] PASS"}

    with patch(
        "kriya.tools.validate.PolymorphicValidator.run_compile_check",
        return_value={"success": True, "output": ""},
    ), patch(
        "kriya.tools.validate.PolymorphicValidator.run_app_sequence",
        side_effect=fake_run_app_sequence,
    ):
        await run_attempt(state, ctx)
        await run_attempt(state, ctx)

    assert pre_call_leftover_content == [None, None], (
        f"attempt 2 should never see attempt 1's leftover tasks.json, got {pre_call_leftover_content}"
    )
    # Cleanup runs after EVERY run_app_sequence call, including the last one -
    # nothing downstream needs a runtime artifact to survive past its own
    # attempt (the apply-to-workspace step only copies source files the
    # Developer wrote, never runtime state), so there's no reason to special-
    # case the final attempt.
    assert not os.path.exists(tasks_json_path)


@pytest.mark.asyncio
async def test_run_attempt_static_check_scopes_likely_files_not_every_written_file(tmp_path):
    """Regression test for a real bug found live (2026-08-13,
    ignite_qpid_protocol): a static_rule_violation's likely_files was
    hardcoded to state.all_files_written (every file this run has ever
    written) instead of the file(s) the violation message actually names -
    even though every StaticCheck's own message already names its implicated
    file(s) by exact known path. Consequence, confirmed live: a targeted
    retry broadcast its per-file "explain the fix" instruction to 6 unrelated
    files, one of which applied a genuinely unrelated edit having nothing to
    do with the actual violation. Here, only ProtocolApp.java calls
    Ignition.start() without closing it - Protocol.java (a plain data class)
    and pom.xml are also written but wholly unrelated, and must NOT appear in
    likely_files."""
    state = GenerationState()
    developer = AsyncMock()
    developer.run_generation = AsyncMock(return_value=[
        {"filepath": "ProtocolApp.java", "content": (
            'public class ProtocolApp {\n'
            '    public static void main(String[] args) throws Exception {\n'
            '        Ignite ignite = Ignition.start();\n'
            '        ignite.getOrCreateCache("x");\n'
            '    }\n'
            '}\n'
        )},
        {"filepath": "Protocol.java", "content": (
            'public class Protocol {\n'
            '    private final String id;\n'
            '    public Protocol(String id) { this.id = id; }\n'
            '}\n'
        )},
        {"filepath": "pom.xml", "content": "<project></project>\n"},
    ])
    ctx = _minimal_attempt_ctx(
        tmp_path, developer=developer,
        architect_files=["ProtocolApp.java", "Protocol.java", "pom.xml"],
        expected_files_upfront=["ProtocolApp.java", "Protocol.java", "pom.xml"],
        architect_basename_to_path={
            "ProtocolApp.java": "ProtocolApp.java", "Protocol.java": "Protocol.java", "pom.xml": "pom.xml",
        },
    )

    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check"), \
         patch("kriya.tools.validate.PolymorphicValidator.run_pom_validate", return_value={"success": True, "output": ""}):
        with pytest.raises(QualityGateFailure) as exc_info:
            await run_attempt(state, ctx)

    assert exc_info.value.failure.type == "static_rule_violation"
    assert "ignite_unclosed_resource" in exc_info.value.failure.message
    assert exc_info.value.failure.likely_files == ["ProtocolApp.java"]
    assert state.gate_outcomes[-1]["likely_files"] == ["ProtocolApp.java"]

@pytest.mark.asyncio
async def test_run_attempt_diagnosis_mismatch_bypassed_when_static_check_genuinely_resolved(tmp_path):
    """End-to-end regression test for the deterministic-validation-first
    bypass added 2026-08-17 (kriya/workflow/attempt.py's
    _diagnosis_mismatch_bypass_reason), for its static_rule_violation half -
    the sibling test for the compile-triggered half is
    test_workflow_diagnosis_mismatch_redirects_back_to_the_same_file above.

    Uses IgniteUnclosedResourceCheck (not BareVerificationMarkerCheck) to
    prove this is a genuinely GENERAL bypass, not just a re-implementation
    of find_edits_ignoring_own_diagnosis's own signal (e) - which is scoped
    specifically to the print-wrap shape and would not itself accept this
    edit. The analysis deliberately quotes only the bare filename
    (`ProtocolApp.java`, no `/` - not excluded by the self-referential-path
    signal either, since that's scoped to actual paths) - a genuine
    mismatch under every existing signal, satisfying none of them - so the
    only thing that can accept this edit is the new bypass re-running
    IgniteUnclosedResourceCheck directly against the proposed content and
    finding the violation genuinely gone."""
    state = GenerationState()
    # Simulates attempt 1 having already happened: the file is on disk with
    # the real violation, already tracked as written, and the retry loop's
    # own budget bookkeeping already knows this retry responds to a
    # static_rule_violation - exactly the state a real attempt 2 would be in.
    unclosed_content = (
        'public class ProtocolApp {\n'
        '    public static void main(String[] args) throws Exception {\n'
        '        Ignite ignite = Ignition.start();\n'
        '        ignite.getOrCreateCache("x");\n'
        '    }\n'
        '}\n'
    )
    with open(os.path.join(str(tmp_path), "ProtocolApp.java"), "w", encoding="utf-8") as fh:
        fh.write(unclosed_content)
    state.all_files_written = {"ProtocolApp.java"}
    state.budgets.last_failure_signature = ("static_rule_violation", ("ignite_unclosed_resource",))

    developer = AsyncMock()
    developer.run_generation = AsyncMock(return_value=[{
        "filepath": "ProtocolApp.java", "content": None,
        "edits": [{
            "search": '        Ignite ignite = Ignition.start();\n        ignite.getOrCreateCache("x");',
            "replace": (
                '        Ignite ignite = Ignition.start();\n'
                '        try {\n'
                '            ignite.getOrCreateCache("x");\n'
                '        } finally {\n'
                '            ignite.close();\n'
                '        }'
            ),
        }],
        "analysis": "The `ProtocolApp.java` resource leak needs explicit cleanup.",
    }])
    ctx = _minimal_attempt_ctx(
        tmp_path, developer=developer,
        architect_files=["ProtocolApp.java"],
        expected_files_upfront=["ProtocolApp.java"],
        architect_basename_to_path={"ProtocolApp.java": "ProtocolApp.java"},
    )

    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check", return_value={"success": True, "output": ""}):
        await run_attempt(state, ctx)  # must NOT raise QualityGateFailure

    with open(os.path.join(str(tmp_path), "ProtocolApp.java"), encoding="utf-8") as fh:
        written = fh.read()
    assert ".close()" in written
    assert not any(g["type"] == "diagnosis_mismatch" for g in state.gate_outcomes)

@pytest.mark.asyncio
async def test_run_attempt_diagnosis_mismatch_bypassed_for_pom_semantic_validation(tmp_path):
    """End-to-end regression test for the pom_semantic_validation half of the
    deterministic-validation-first bypass, added 2026-08-17 after a real live
    false positive (ignite_qpid_person, run b-10o): a correct edit wrapped a
    bare, dangling <dependency> fragment in a full, valid Maven <project> POM
    - exactly what its own FIX ANALYSIS described - but was rejected 4
    consecutive times because the analysis quoted the tag as `<project>`
    (no attributes) while the real fix wrote `<project xmlns="...">` (a real
    Maven POM always declares a namespace), so a literal substring check for
    the bare quoted tag never matched. The underlying gap
    (find_edits_ignoring_own_diagnosis's own prose-matching) is real and
    reproduced directly against the actual incident text below; this test
    confirms the bypass, not a 6th signal, is what makes the edit through -
    same tradeoff as the compile case, since a real compile gate runs right
    after regardless."""
    state = GenerationState()
    dangling_content = (
        '<dependency>\n'
        '    <groupId>com.fasterxml.jackson.core</groupId>\n'
        '    <artifactId>jackson-databind</artifactId>\n'
        '    <version>2.15.2</version>\n'
        '</dependency>\n'
    )
    with open(os.path.join(str(tmp_path), "pom.xml"), "w", encoding="utf-8") as fh:
        fh.write(dangling_content)
    state.all_files_written = {"pom.xml"}
    state.budgets.last_failure_signature = ("pom_semantic_validation", ("mvn_validate_failed",))

    fixed_pom = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<project xmlns="http://maven.apache.org/POM/4.0.0">\n'
        '    <modelVersion>4.0.0</modelVersion>\n'
        '    <groupId>com.example</groupId>\n'
        '    <artifactId>app</artifactId>\n'
        '    <version>1.0-SNAPSHOT</version>\n'
        '    <dependencies>\n'
        '        <dependency>\n'
        '            <groupId>com.fasterxml.jackson.core</groupId>\n'
        '            <artifactId>jackson-databind</artifactId>\n'
        '            <version>2.15.2</version>\n'
        '        </dependency>\n'
        '    </dependencies>\n'
        '</project>\n'
    )
    developer = AsyncMock()
    developer.run_generation = AsyncMock(return_value=[{
        "filepath": "pom.xml", "content": None,
        "edits": [{"search": dangling_content.strip(), "replace": fixed_pom.strip()}],
        "analysis": (
            "The error indicates that the pom.xml file is malformed - it starts directly "
            "with a `<dependency>` element instead of the required Maven POM structure "
            "with `<project>` as the root element. I need to wrap the existing dependency "
            "in the proper Maven project XML structure."
        ),
    }])
    ctx = _minimal_attempt_ctx(
        tmp_path, developer=developer,
        architect_files=["pom.xml"],
        expected_files_upfront=["pom.xml"],
        architect_basename_to_path={"pom.xml": "pom.xml"},
    )

    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check", return_value={"success": True, "output": ""}):
        await run_attempt(state, ctx)  # must NOT raise QualityGateFailure

    with open(os.path.join(str(tmp_path), "pom.xml"), encoding="utf-8") as fh:
        written = fh.read()
    assert "<project" in written
    assert not any(g["type"] == "diagnosis_mismatch" for g in state.gate_outcomes)

@pytest.mark.asyncio
async def test_run_attempt_diagnosis_mismatch_bounded_veto_bypasses_second_rejection(tmp_path):
    """Regression test for the bounded-veto policy added 2026-08-17
    (kriya/workflow/state.py's diagnosis_mismatch_veto_counts,
    kriya/workflow/attempt.py's _diagnosis_mismatch_bypass_reason): this
    check may reject a given FILE's edit at most once per run, regardless of
    fail_type - the safety net underneath the fail-type-scoped bypasses
    (compile/static_rule_violation/pom_semantic_validation), for every OTHER
    fail_type where no cheap re-check exists. Motivated directly by
    ignite_qpid_person run b-10o, where this exact check rejected the same
    correct fix 4 CONSECUTIVE times before the run's wall-clock budget ran
    out.

    Deliberately uses "run_verification" as the failure signature - NOT one
    of the 3 fail_types with their own cheap-recheck bypass - to prove this
    is a real, fail-type-independent safety net, not just a restatement of
    the existing dispatch."""
    state = GenerationState()
    original_content = "class App {\n    int getValue() { return 1; }\n}\n"
    with open(os.path.join(str(tmp_path), "App.java"), "w", encoding="utf-8") as fh:
        fh.write(original_content)
    state.all_files_written = {"App.java"}
    state.budgets.last_failure_signature = ("run_verification", ("dummy",))

    mismatched_response = [{
        "filepath": "App.java", "content": None,
        "edits": [{
            # A real, non-identical change (a comment appended) - NOT a
            # whole-response no-op, so this exercises Layer 2's bypass
            # specifically, not the separate Layer 0 no-op check.
            "search": "int getValue() { return 1; }",
            "replace": "int getValue() { return 1; } // unchanged",
        }],
        "analysis": "I renamed `getValue` to `fetchValue` to match the goal's naming convention.",
    }]
    developer = AsyncMock()
    developer.run_generation = AsyncMock(return_value=mismatched_response)
    ctx = _minimal_attempt_ctx(
        tmp_path, developer=developer,
        architect_files=["App.java"],
        expected_files_upfront=["App.java"],
        architect_basename_to_path={"App.java": "App.java"},
    )

    # First attempt: no veto recorded yet for this file - rejects exactly as
    # find_edits_ignoring_own_diagnosis's own logic always has.
    with pytest.raises(QualityGateFailure) as exc_info:
        await run_attempt(state, ctx)
    assert exc_info.value.failure.type == "diagnosis_mismatch"
    assert state.budgets.diagnosis_mismatch_veto_counts.get("App.java") == 1

    # Second attempt: the model repeats the IDENTICAL non-fix (as it did 4x
    # in the real b-10o incident) - the bounded veto must bypass this time
    # regardless of fail_type, deferring to the real compile gate (mocked to
    # pass here, matching a case where the "mismatch" was harmless all along).
    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check", return_value={"success": True, "output": ""}):
        await run_attempt(state, ctx)  # must NOT raise

    diagnosis_mismatch_outcomes = [g for g in state.gate_outcomes if g["type"] == "diagnosis_mismatch"]
    assert len(diagnosis_mismatch_outcomes) == 1, "must never reject the same file's diagnosis a second time"

@pytest.mark.asyncio
async def test_run_attempt_diagnosis_mismatch_bypassed_for_targeted_test(tmp_path):
    """End-to-end regression test for the targeted_test half of the
    deterministic-validation-first bypass, added 2026-08-17 alongside the
    bounded-veto policy: run_tests(target_test=...) (kriya/tools/validate.py)
    already scopes a rerun to the ONE test file extract_target_test()
    identified, not the whole suite, and that real rerun happens immediately
    after this per-file write loop regardless, in the same attempt - so a
    targeted_test-triggered retry gets the same bypass treatment as
    compile/pom_semantic_validation, for the same reason."""
    state = GenerationState()
    store_content = "def add(x):\n    return x + 1\n"
    with open(os.path.join(str(tmp_path), "store.py"), "w", encoding="utf-8") as fh:
        fh.write(store_content)
    with open(os.path.join(str(tmp_path), "test_store.py"), "w", encoding="utf-8") as fh:
        fh.write("def test_add():\n    assert True\n")
    state.all_files_written = {"store.py", "test_store.py"}
    state.budgets.last_failure_signature = ("targeted_test", ("dummy",))
    state.error_context = "test_store.py::test_add failed"

    developer = AsyncMock()
    developer.run_generation = AsyncMock(return_value=[{
        "filepath": "store.py", "content": None,
        # A real, non-identical change - NOT a whole-response no-op, so this
        # exercises the targeted_test bypass specifically, not the separate
        # Layer 0 no-op check.
        "edits": [{"search": "def add(x):", "replace": "def add(x):  # unchanged"}],
        "analysis": "I renamed `add` to `increment` to make the intent clearer.",
    }])
    ctx = _minimal_attempt_ctx(
        tmp_path, developer=developer,
        architect_files=["store.py", "test_store.py"],
        expected_files_upfront=["store.py", "test_store.py"],
        architect_basename_to_path={"store.py": "store.py", "test_store.py": "test_store.py"},
    )

    with patch(
        "kriya.tools.validate.PolymorphicValidator.run_compile_check",
        return_value={"success": True, "output": ""},
    ), patch(
        "kriya.tools.validate.PolymorphicValidator.run_tests",
        return_value={"success": True, "output": ""},
    ):
        await run_attempt(state, ctx)  # must NOT raise

    assert not any(g["type"] == "diagnosis_mismatch" for g in state.gate_outcomes)

@pytest.mark.asyncio
async def test_run_attempt_rejects_a_whole_response_no_op_edit(tmp_path):
    """End-to-end regression test for the Layer 0 no-op check added
    2026-08-17 (find_whole_response_no_op, kriya/workflow/attribution.py),
    reproducing the real b-10l incident this closes (design doc §7.32): a
    targeted retry whose SEARCH and REPLACE were byte-identical, which the
    model itself mislabeled as "replacing the entire file content." This
    must be rejected BEFORE Layer 2 (diagnosis_mismatch) ever runs - and,
    unlike Layer 2, unconditionally, with no fail-type bypass and no
    bounded-veto interaction, since it's purely structural (search != replace
    is an objective fact, not a fuzzy prose judgment)."""
    state = GenerationState()
    xml_content = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<beans><bean id="x" class="y"/></beans>\n'
    )
    with open(os.path.join(str(tmp_path), "applicationContext.xml"), "w", encoding="utf-8") as fh:
        fh.write(xml_content)
    state.all_files_written = {"applicationContext.xml"}
    # Deliberately a fail_type that DOES have its own cheap-recheck bypass
    # (compile) - proves Layer 0 fires unconditionally, before that dispatch
    # even runs, not just for the fail types Layer 2 doesn't cover.
    state.budgets.last_failure_signature = ("compile", ("dummy",))

    developer = AsyncMock()
    developer.run_generation = AsyncMock(return_value=[{
        "filepath": "applicationContext.xml", "content": None,
        "edits": [{"search": xml_content, "replace": xml_content}],
        "analysis": "I will replace the entire file content with a clean, exact copy to strip hidden characters.",
    }])
    ctx = _minimal_attempt_ctx(
        tmp_path, developer=developer,
        architect_files=["applicationContext.xml"],
        expected_files_upfront=["applicationContext.xml"],
        architect_basename_to_path={"applicationContext.xml": "applicationContext.xml"},
    )

    with pytest.raises(QualityGateFailure) as exc_info:
        await run_attempt(state, ctx)

    assert exc_info.value.failure.type == "no_op_edit"
    assert exc_info.value.failure.likely_files == ["applicationContext.xml"]
    assert not any(g["type"] == "diagnosis_mismatch" for g in state.gate_outcomes)

@pytest.mark.asyncio
async def test_run_attempt_no_op_check_does_not_flag_a_companion_edit(tmp_path):
    """Negative case for the same Layer 0 check: a response with ONE no-op
    edit sitting alongside a genuinely real edit (the b-10f companion-edit
    shape, §7.29) must pass through Layer 0 untouched - only a response
    where every edit does nothing is a whole-response no-op."""
    state = GenerationState()
    java_content = (
        'import bad.pkg.Cache;\n'
        'class App {\n'
        '  Cache c = get();\n'
        '}\n'
    )
    with open(os.path.join(str(tmp_path), "App.java"), "w", encoding="utf-8") as fh:
        fh.write(java_content)
    state.all_files_written = {"App.java"}

    developer = AsyncMock()
    developer.run_generation = AsyncMock(return_value=[{
        "filepath": "App.java", "content": None,
        "edits": [
            {"search": "import bad.pkg.Cache;", "replace": "import good.pkg.Cache;"},
            {"search": "  Cache c = get();", "replace": "  Cache c = get();"},
        ],
        "analysis": "Fixed the bad Cache import.",
    }])
    ctx = _minimal_attempt_ctx(
        tmp_path, developer=developer,
        architect_files=["App.java"],
        expected_files_upfront=["App.java"],
        architect_basename_to_path={"App.java": "App.java"},
    )

    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check", return_value={"success": True, "output": ""}):
        await run_attempt(state, ctx)  # must NOT raise

    assert not any(g["type"] == "no_op_edit" for g in state.gate_outcomes)

@pytest.mark.asyncio
async def test_run_attempt_isolated_success_passes_quality_gates(tmp_path):
    """Mirror of the failure-path test above: a clean compile with no tests
    and Runtime Verification disabled returns normally (no exception), having
    recorded exactly one passing compile gate outcome - proving the isolated
    call path works for the success case too, not just failures."""
    state = GenerationState()
    developer = AsyncMock()
    developer.run_generation = AsyncMock(return_value=[
        {"filepath": "app.py", "content": "print('hi')\n"}
    ])
    kernel = Kernel(config=AppConfig())
    kernel.config.autonomy.run_verification_enabled = False
    ctx = _minimal_attempt_ctx(tmp_path, developer=developer, kernel=kernel)

    with patch(
        "kriya.tools.validate.PolymorphicValidator.run_compile_check",
        return_value={"success": True, "output": "compiled fine"},
    ):
        await run_attempt(state, ctx)  # must not raise

    assert state.gate_outcomes == [{
        "attempt": 1, "type": "compile", "success": True, "output": "compiled fine",
    }]
    assert state.last_attempt_mode == "full_set"

@pytest.mark.asyncio
async def test_run_attempt_scopes_first_full_set_attempt_to_implicated_files_after_budget_exhaustion(tmp_path):
    """Regression test for a real live gap found this session (2026-08-14,
    spikes/eval_harness/runs/a-3 and a-4, ignite_qpid_protocol): targeted_retry_count/
    fallback_targeted_attempted are single counters shared across the WHOLE run, not
    per-failure - a run that spends its entire targeted budget resolving one bug has
    zero scoped-retry runway left for a completely different, freshly-diagnosed
    failure that arrives right after, even when THAT failure has a precise
    single-file locator. Confirmed live: a-4's targeted budget was spent fixing an
    unclosed-Ignite-resource bug across 4 targeted attempts + 1 fallback-targeted
    attempt; the very next, unrelated compile error (`Protocol.java:[17,5] variable
    dataLength might not have been initialized`) then fell straight into an unscoped
    full-file-set walk on the slow fallback model - one multi-minute completion PER
    FILE (9 files) for a fix that only ever needed one - and the run timed out.

    Confirms the FIRST full-set attempt reached via budget exhaustion (not via this
    exact failure resisting narrow scoping) still passes
    known_target_files=state.last_implicated_files, instead of falling through to an
    unscoped full-file-set request."""
    state = GenerationState()
    state.budgets.targeted_retry_count = 3
    state.budgets.fallback_targeted_attempted = True
    state.last_implicated_files = ["Protocol.java"]
    state.all_files_written = {"Protocol.java", "ProtocolApp.java", "ProtocolParser.java"}
    developer = AsyncMock()
    developer.run_generation = AsyncMock(return_value=[
        {"filepath": "Protocol.java", "content": "class Protocol {}\n"}
    ])
    kernel = Kernel(config=AppConfig())
    kernel.config.autonomy.run_verification_enabled = False
    ctx = _minimal_attempt_ctx(
        tmp_path, developer=developer, kernel=kernel,
        expected_files_upfront=[], targeted_max_retries=3,
        architect_files=["Protocol.java", "ProtocolApp.java", "ProtocolParser.java"],
    )

    with patch(
        "kriya.tools.validate.PolymorphicValidator.run_compile_check",
        return_value={"success": True, "output": ""},
    ):
        await run_attempt(state, ctx)

    assert state.last_attempt_mode == "full_set"
    call_kwargs = developer.run_generation.call_args.kwargs
    assert call_kwargs["known_target_files"] == ["Protocol.java"]

@pytest.mark.asyncio
async def test_run_attempt_does_not_scope_a_later_full_set_attempt(tmp_path):
    """Sibling/negative case: the scoping above is deliberately gated to the FIRST
    full-set attempt only (state.budgets.retry_count == 0) - once a scoped full-set
    attempt has already been tried and the same failure persists (retry_count now
    >= 1), the existing "broaden to a clean full regeneration" escape hatch must
    still apply completely unchanged, exactly as before this fix - a failure that
    genuinely resists narrow scoping still gets a real clean-slate attempt, not an
    endless narrower and narrower retry on the same wrong diagnosis."""
    state = GenerationState()
    state.budgets.retry_count = 1
    state.budgets.targeted_retry_count = 3
    state.budgets.fallback_targeted_attempted = True
    state.last_implicated_files = ["Protocol.java"]
    state.all_files_written = {"Protocol.java", "ProtocolApp.java", "ProtocolParser.java"}
    developer = AsyncMock()
    developer.run_generation = AsyncMock(return_value=[
        {"filepath": "Protocol.java", "content": "class Protocol {}\n"},
        {"filepath": "ProtocolApp.java", "content": "class ProtocolApp {}\n"},
        {"filepath": "ProtocolParser.java", "content": "class ProtocolParser {}\n"},
    ])
    kernel = Kernel(config=AppConfig())
    kernel.config.autonomy.run_verification_enabled = False
    ctx = _minimal_attempt_ctx(
        tmp_path, developer=developer, kernel=kernel,
        expected_files_upfront=[], targeted_max_retries=3,
        architect_files=["Protocol.java", "ProtocolApp.java", "ProtocolParser.java"],
    )

    with patch(
        "kriya.tools.validate.PolymorphicValidator.run_compile_check",
        return_value={"success": True, "output": ""},
    ):
        await run_attempt(state, ctx)

    call_kwargs = developer.run_generation.call_args.kwargs
    assert call_kwargs["known_target_files"] is None

@pytest.mark.asyncio
async def test_run_attempt_reuses_planner_code_when_full_coverage(tmp_path):
    """The actual payoff: when the Planner's own plan text already has
    complete code for every expected file, run_attempt() must use it
    directly - developer.run_generation() should never be called at all -
    while still going through the exact same compile gate as any other
    attempt."""
    state = GenerationState()
    developer = AsyncMock()
    developer.run_generation = AsyncMock(
        side_effect=AssertionError("developer.run_generation() must not be called when Planner coverage is complete")
    )
    plan = (
        "### app.py\n```python\nprint('hi')\n```\n"
    )
    kernel = Kernel(config=AppConfig())
    kernel.config.autonomy.run_verification_enabled = False
    ctx = _minimal_attempt_ctx(
        tmp_path, developer=developer, plan=plan, kernel=kernel,
        architect_files=["app.py"], expected_files_upfront=["app.py"],
    )

    with patch(
        "kriya.tools.validate.PolymorphicValidator.run_compile_check",
        return_value={"success": True, "output": "compiled fine"},
    ):
        await run_attempt(state, ctx)

    developer.run_generation.assert_not_called()
    assert (tmp_path / "app.py").read_text() == "print('hi')\n"
    assert state.gate_outcomes[-1] == {
        "attempt": 1, "type": "compile", "success": True, "output": "compiled fine",
    }

@pytest.mark.asyncio
async def test_run_attempt_falls_back_to_developer_when_planner_coverage_partial(tmp_path):
    """A Planner plan covering only SOME of the expected files must not be
    used at all (deliberately all-or-nothing) - falls through to the normal
    Developer generation path unchanged."""
    state = GenerationState()
    developer = AsyncMock()
    developer.run_generation = AsyncMock(return_value=[
        {"filepath": "app.py", "content": "print('from developer')\n"},
        {"filepath": "helper.py", "content": "print('helper from developer')\n"},
    ])
    plan = "### app.py\n```python\nprint('from planner')\n```\n"  # missing helper.py
    kernel = Kernel(config=AppConfig())
    kernel.config.autonomy.run_verification_enabled = False
    ctx = _minimal_attempt_ctx(
        tmp_path, developer=developer, plan=plan, kernel=kernel,
        architect_files=["app.py", "helper.py"], expected_files_upfront=["app.py", "helper.py"],
    )

    with patch(
        "kriya.tools.validate.PolymorphicValidator.run_compile_check",
        return_value={"success": True, "output": "compiled fine"},
    ):
        await run_attempt(state, ctx)

    developer.run_generation.assert_called_once()
    assert (tmp_path / "app.py").read_text() == "print('from developer')\n"

@pytest.mark.asyncio
async def test_run_attempt_does_not_reuse_planner_code_on_retry(tmp_path):
    """Planner-code reuse is scoped to attempt 1 only (retry_count == 0) -
    a plan that already led to a failed attempt isn't a trustworthy source
    for a fresh one, matching the same scoping the known_target_files
    shortcut already uses."""
    state = GenerationState()
    state.budgets.retry_count = 1
    developer = AsyncMock()
    developer.run_generation = AsyncMock(return_value=[
        {"filepath": "app.py", "content": "print('from developer')\n"}
    ])
    plan = "### app.py\n```python\nprint('from planner')\n```\n"
    kernel = Kernel(config=AppConfig())
    kernel.config.autonomy.run_verification_enabled = False
    ctx = _minimal_attempt_ctx(
        tmp_path, developer=developer, plan=plan, kernel=kernel,
        architect_files=["app.py"], expected_files_upfront=["app.py"],
    )

    with patch(
        "kriya.tools.validate.PolymorphicValidator.run_compile_check",
        return_value={"success": True, "output": "compiled fine"},
    ):
        await run_attempt(state, ctx)

    developer.run_generation.assert_called_once()
    assert (tmp_path / "app.py").read_text() == "print('from developer')\n"

@pytest.mark.asyncio
async def test_run_attempt_persists_grader_reasoning_on_run_verification_failure(tmp_path):
    """Regression test for a real bug found live, 2026-08-11
    (kriya-oneshot-protocol-ignite-qpid audit): Failure.to_gate_outcome()'s
    "output" field always preferred raw_output over message, so the grader's
    own synthesized reasoning - explicitly appended to the persisted output
    on the PASSED path (a few lines below in attempt.py) - was silently
    absent from the persisted gate_outcome whenever run_verification FAILED,
    even though it was computed. A human (or a future debugging session)
    inspecting a failed run_verification gate_outcome in traces.db would see
    only the raw captured output, never the grader's diagnosis of what
    actually went wrong - confirmed directly against a real trace.

    Mock shape corrected 2026-08-15 (independent brutal review finding #1):
    this used to mock run_app_sequence with success=True/returncode=1 - a
    shape the REAL run_app_sequence() can never produce (success reflects
    the whole sequence, never True alongside a nonzero returncode with no
    timeout), so this test was actually exercising the clean-run branch in
    attempt.py, not the plain-nonzero-exit branch its own scenario (a crash)
    describes. success=False is what a real crash actually looks like, and
    is exactly the shape that branch had ZERO real test coverage for before
    that finding - now this test genuinely exercises it."""
    state = GenerationState()
    developer = AsyncMock()
    developer.run_generation = AsyncMock(return_value=[{"filepath": "app.py", "content": "print('hi')\n"}])
    run_verifier = AsyncMock()
    run_verifier.judge = AsyncMock(return_value={
        "should_run": True,
        "run_commands": [["python3", "app.py"]],
        "command_source": "goal_explicit",
        "success_criteria": "Prints hi",
    })
    run_verifier.grade = AsyncMock(return_value={
        "passed": False,
        "reasoning": "The output shows a real defect: the app crashed instead of printing hi.",
    })
    ctx = _minimal_attempt_ctx(
        tmp_path, developer=developer, run_verifier=run_verifier,
        architect_files=["app.py"], expected_files_upfront=["app.py"],
    )

    with patch(
        "kriya.tools.validate.PolymorphicValidator.run_compile_check",
        return_value={"success": True, "output": "compiled fine"},
    ), patch(
        "kriya.tools.validate.PolymorphicValidator.run_app_sequence",
        return_value={"success": False, "timed_out": False, "returncode": 1, "output": "Traceback: crashed"},
    ):
        with pytest.raises(QualityGateFailure):
            await run_attempt(state, ctx)

    run_verification_outcome = next(g for g in state.gate_outcomes if g["type"] == "run_verification")
    assert "[Grader reasoning]" in run_verification_outcome["output"]
    assert "crashed instead of printing hi" in run_verification_outcome["output"]

@pytest.mark.asyncio
async def test_handle_attempt_failure_increments_retry_count_and_continues(tmp_path):
    """The Slice 3 payoff: retry-decision logic (budget accounting, the
    continue-vs-stop call) can now be tested by calling handle_attempt_failure()
    directly with a hand-built GenerationState/AttemptContext and a real
    QualityGateFailure - no WorkflowEngine, no retry loop, no worktree."""
    state = GenerationState()
    state.attempt_number = 1
    state.last_attempt_mode = "full_set"
    ctx = _minimal_attempt_ctx(tmp_path, max_retries=4)
    exc = QualityGateFailure(Failure(
        type="compile", message="COMPILATION FAILURE: cannot find symbol",
        raw_output="cannot find symbol",
    ))

    should_break = await handle_attempt_failure(state, ctx, exc)

    assert should_break is False
    assert state.budgets.retry_count == 1
    assert state.environment_failure is None
    assert state.gate_outcomes[-1]["type"] == "compile"
    assert state.gate_outcomes[-1]["success"] is False

@pytest.mark.asyncio
async def test_handle_attempt_failure_stops_immediately_on_environment_failure(tmp_path):
    """An environment/toolchain failure (a JVM that can't even start) must stop
    the retry loop on the very first attempt, well before the retry budget is
    exhausted - no amount of code regeneration can fix it."""
    state = GenerationState()
    state.attempt_number = 1
    state.last_attempt_mode = "full_set"
    ctx = _minimal_attempt_ctx(tmp_path, max_retries=4)
    exc = QualityGateFailure(Failure(
        type="compile",
        message="Error occurred during initialization of VM\nCould not reserve enough space",
        raw_output="Error occurred during initialization of VM",
    ))

    should_break = await handle_attempt_failure(state, ctx, exc)

    assert should_break is True
    assert state.environment_failure is not None
    assert state.budgets.retry_count == 1

@pytest.mark.asyncio
async def test_workflow_strips_jdk_incompatible_jvm_flag_before_running(tmp_path):
    """Workflow-level regression test: the forbidden flag must actually be
    gone from the worktree's pom.xml before run_app_sequence executes (not
    just correct at the _strip_jdk_incompatible_jvm_flags unit level), and
    the correction must be visible via toolchain_warning regardless of the
    run's own outcome - same reporting path _check_java_toolchain_mismatch's
    warning already uses."""
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write pom.xml",
        "Review: Approved",
    ])
    we = WorkflowEngine(kernel, llm)
    we.developer.run_generation = AsyncMock(return_value=[
        {"filepath": "pom.xml", "content": _POM_WITH_SECURITY_MANAGER_FLAG}
    ])
    we.run_verifier.judge = AsyncMock(return_value={
        "should_run": True,
        "run_commands": [["mvn", "-e", "exec:exec"]],
        "command_source": "goal_explicit",
        "success_criteria": "Output contains [SUCCESS]",
    })
    we.run_verifier.grade = AsyncMock(return_value={
        "passed": True, "reasoning": "Output contains the expected [SUCCESS] line.",
    })

    with patch("kriya.tools.validate.check_java_toolchain", return_value={
        "java_found": True, "java_version": "26",
        "mvn_found": True, "mvn_java_version": "26",
        "mismatch": False,
    }), patch(
        "kriya.tools.validate.PolymorphicValidator.run_compile_check",
        return_value={"success": True, "output": "Maven compilation succeeded."},
    ), patch(
        "kriya.tools.validate.PolymorphicValidator.run_pom_validate",
        return_value={"success": True, "output": ""},
    ), patch(
        "kriya.tools.validate.PolymorphicValidator.run_tests",
        return_value={"success": True, "output": ""},
    ), patch(
        "kriya.tools.validate.PolymorphicValidator.run_app_sequence",
        return_value={"success": True, "timed_out": False, "returncode": 0, "output": "[SUCCESS] it worked"},
    ):
        res = await we.run_generation_workflow(
            goal="Create a Java app using Maven; run mvn exec:exec, it should print [SUCCESS]",
            workspace_path=str(tmp_path),
        )

    assert res["quality_gates_passed"] is True
    assert res["toolchain_warning"] is not None
    assert "java.security.manager" in res["toolchain_warning"]
    final_pom = (tmp_path / "pom.xml").read_text()
    assert "-Djava.security.manager=allow" not in final_pom

@pytest.mark.asyncio
async def test_workflow_jvm_flag_strip_decides_against_override_not_mvn_default(tmp_path):
    """Workflow-level regression test for a real bug found live (2026-08-07,
    ignite_qpid_person re-run immediately after the JAVA_HOME override
    feature shipped): with java_home_override active, the flag-strip call
    used to decide against mvn's UNMODIFIED default JDK (26 here) via a
    fresh, independent check_java_toolchain() call, ignoring that this same
    run was already forcing every Maven subprocess onto JDK 17 via
    JAVA_HOME. Confirms the wiring fix: with the override active and its
    target (17) reported as toolchain['java_version'], the flag - required
    on 17, forbidden only on 24+ - must survive, even though mvn's own
    untouched default is 26."""
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write pom.xml",
        "Review: Approved",
    ])
    we = WorkflowEngine(kernel, llm)
    we.developer.run_generation = AsyncMock(return_value=[
        {"filepath": "pom.xml", "content": _POM_WITH_SECURITY_MANAGER_FLAG}
    ])
    we.run_verifier.judge = AsyncMock(return_value={
        "should_run": True,
        "run_commands": [["mvn", "-e", "exec:exec"]],
        "command_source": "goal_explicit",
        "success_criteria": "Output contains [SUCCESS]",
    })
    we.run_verifier.grade = AsyncMock(return_value={
        "passed": True, "reasoning": "Output contains the expected [SUCCESS] line.",
    })

    with patch(
        "kriya.workflow.attempt._resolve_java_home_override",
        return_value="/Library/Java/JavaVirtualMachines/temurin-17.jdk/Contents/Home",
    ), patch("kriya.tools.validate.check_java_toolchain", return_value={
        "java_found": True, "java_version": "17",
        "mvn_found": True, "mvn_java_version": "26",
        "mismatch": True,
    }), patch(
        "kriya.tools.validate.PolymorphicValidator.run_compile_check",
        return_value={"success": True, "output": "Maven compilation succeeded."},
    ), patch(
        "kriya.tools.validate.PolymorphicValidator.run_pom_validate",
        return_value={"success": True, "output": ""},
    ), patch(
        "kriya.tools.validate.PolymorphicValidator.run_tests",
        return_value={"success": True, "output": ""},
    ), patch(
        "kriya.tools.validate.PolymorphicValidator.run_app_sequence",
        return_value={"success": True, "timed_out": False, "returncode": 0, "output": "[SUCCESS] it worked"},
    ):
        res = await we.run_generation_workflow(
            goal="Create a Java app using Maven, targeting Java 17; run mvn exec:exec, it should print [SUCCESS]",
            workspace_path=str(tmp_path),
        )

    assert res["quality_gates_passed"] is True
    # The mismatch warning itself still fires (java/mvn genuinely disagree on
    # this machine), but the flag-strip note must NOT - the effective target
    # under the override is JDK 17, where this flag is required.
    if res["toolchain_warning"]:
        assert "java.security.manager" not in res["toolchain_warning"]
    final_pom = (tmp_path / "pom.xml").read_text()
    assert "-Djava.security.manager=allow" in final_pom

@pytest.mark.asyncio
async def test_workflow_pins_exec_plugin_executable_to_resolved_jdk_in_applied_workspace(tmp_path):
    """Workflow-level regression test for a real bug found live, 2026-08-11
    (kriya-staged-protocol-ignite-qpid): Runtime Verification reported
    PASSED because Kriya forces JAVA_HOME onto the goal-stated target JDK for
    its OWN subprocess calls only - the delivered pom.xml still had a bare
    <executable>java</executable>, so a human running the identical command
    later (on a machine where 'mvn' defaults to a genuinely incompatible JDK)
    got a crash Kriya's own verification never saw. Confirms the fix reaches
    the FINAL APPLIED workspace file, not just the worktree copy."""
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write pom.xml",
        "Review: Approved",
    ])
    we = WorkflowEngine(kernel, llm)
    we.developer.run_generation = AsyncMock(return_value=[
        {"filepath": "pom.xml", "content": _POM_WITH_SECURITY_MANAGER_FLAG}
    ])
    we.run_verifier.judge = AsyncMock(return_value={
        "should_run": True,
        "run_commands": [["mvn", "-e", "exec:exec"]],
        "command_source": "goal_explicit",
        "success_criteria": "Output contains [SUCCESS]",
    })
    we.run_verifier.grade = AsyncMock(return_value={
        "passed": True, "reasoning": "Output contains the expected [SUCCESS] line.",
    })

    with patch(
        "kriya.workflow.attempt._resolve_java_home_override",
        return_value="/Library/Java/JavaVirtualMachines/temurin-17.jdk/Contents/Home",
    ), patch("kriya.tools.validate.check_java_toolchain", return_value={
        "java_found": True, "java_version": "17",
        "mvn_found": True, "mvn_java_version": "26",
        "mismatch": True,
    }), patch(
        "kriya.tools.validate.PolymorphicValidator.run_compile_check",
        return_value={"success": True, "output": "Maven compilation succeeded."},
    ), patch(
        "kriya.tools.validate.PolymorphicValidator.run_pom_validate",
        return_value={"success": True, "output": ""},
    ), patch(
        "kriya.tools.validate.PolymorphicValidator.run_tests",
        return_value={"success": True, "output": ""},
    ), patch(
        "kriya.tools.validate.PolymorphicValidator.run_app_sequence",
        return_value={"success": True, "timed_out": False, "returncode": 0, "output": "[SUCCESS] it worked"},
    ):
        res = await we.run_generation_workflow(
            goal="Create a Java app using Maven, targeting Java 17; run mvn exec:exec, it should print [SUCCESS]",
            workspace_path=str(tmp_path),
        )

    assert res["quality_gates_passed"] is True
    final_pom = (tmp_path / "pom.xml").read_text()
    assert (
        "<executable>/Library/Java/JavaVirtualMachines/temurin-17.jdk/Contents/Home/bin/java</executable>"
        in final_pom
    )

@pytest.mark.asyncio
async def test_workflow_recovers_missing_build_manifest_never_requested_by_architect(tmp_path):
    """Workflow-level regression test for the real bug (2026-08-07,
    kriya-protocol-parser-app): even when the Architect's design never
    mentions pom.xml at all (so IncompleteGenerationError never fires,
    since it only recovers a file the design DID list), a compile failure
    shaped like a missing-dependency error with no pom.xml/build.gradle
    present must redirect the NEXT attempt at generating pom.xml
    specifically - not keep re-targeting Main.java, which already
    correctly declined to fix an out-of-scope dependency problem on every
    prior attempt."""
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write Main.java",  # deliberately never mentions pom.xml
        "Review: Approved",
    ])
    we = WorkflowEngine(kernel, llm)
    we.developer.run_generation = AsyncMock(side_effect=[
        [{
            "filepath": "src/main/java/com/example/Main.java",
            "content": "import org.springframework.context.ApplicationContext;\npublic class Main {}",
        }],
        [{"filepath": "pom.xml", "content": "<project></project>"}],
    ])
    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check") as mock_compile, \
         patch("kriya.tools.validate.PolymorphicValidator.run_pom_validate", return_value={"success": True, "output": ""}), \
         patch("kriya.tools.validate.PolymorphicValidator.run_tests", return_value={"success": True, "output": ""}):
        mock_compile.side_effect = [
            {
                "success": False,
                "output": (
                    "Java compilation failed:\n"
                    "Main.java:1: error: package org.springframework.context does not exist"
                ),
            },
            {"success": True, "output": "Maven compilation succeeded."},
        ]
        res = await we.run_generation_workflow(goal="Create a Java app", workspace_path=str(tmp_path))

    assert res["quality_gates_passed"] is True
    second_call_kwargs = we.developer.run_generation.call_args_list[1].kwargs
    assert second_call_kwargs["known_target_files"] == ["pom.xml"]

@pytest.mark.asyncio
async def test_workflow_run_verification_judgment_cached_across_retry_attempts(tmp_path):
    """RunVerifierAgent.judge() was previously re-invoked (a real LLM round-trip)
    on every single attempt that reached runtime verification, even though the
    goal/design driving 'should we run this, and how' don't change between
    retries - confirmed live that once correct, the judgment stayed identical
    and correct across repeated attempts, so repeating the call only wasted
    latency. Must be called exactly once across two attempts that both reach
    this stage; grade() (evaluates THIS attempt's actual output) must still be
    called fresh each time. Asserts directly on call counts of the real
    RunVerifierAgent methods (not inferred from llm.complete side_effect-list
    exhaustion), since a first draft of this test using the indirect approach
    turned out to pass even without the fix - a stray judge() call fed
    grade()'s JSON shape, which judge() parses leniently (defaults should_run
    to False rather than raising) rather than crashing, silently skipping
    verification on attempt 2 instead of correctly re-running it."""
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    file_content_response = "print('[SUCCESS] it worked')\n"

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",     # Planner
        "Design: Write app.py",   # Architect
        file_content_response,    # Developer attempt 1
        file_content_response,    # Developer attempt 2 (full-set retry)
        "Review: Approved",       # Reviewer
    ])

    we = WorkflowEngine(kernel, llm)
    we.run_verifier.judge = AsyncMock(return_value={
        "should_run": True,
        "run_commands": [[sys.executable, "app.py"]],
        "command_source": "goal_explicit",
        "success_criteria": "Output contains [SUCCESS]",
    })
    we.run_verifier.grade = AsyncMock(side_effect=[
        {"passed": False, "reasoning": "Missing expected marker."},
        {"passed": True, "reasoning": "Output contains the expected [SUCCESS] line."},
    ])

    res = await we.run_generation_workflow(
        goal="Run with python app.py; it should print [SUCCESS]",
        workspace_path=str(tmp_path)
    )

    assert res["quality_gates_passed"] is True
    we.run_verifier.judge.assert_called_once()
    assert we.run_verifier.grade.call_count == 2

@pytest.mark.asyncio
async def test_workflow_scopes_retry_to_grader_likely_files_on_run_verification_failure(tmp_path):
    """A compile error always names its own broken file (file:[line,col]),
    which is what lets extract_implicated_files() scope a retry to just that
    file. A runtime verification failure's captured output structurally
    never does (broker banners, SLF4J lines with no .java suffix) - without
    the grader naming a likely-responsible file directly, this failure class
    always fell back to a blind full-set retry with none of the FIX ANALYSIS
    forcing instruction, anchored-edit preference, or file-scoped LSP
    grounding a compile failure gets. Confirms the grader's likely_files
    reaches the next attempt's implicated_files with no extra plumbing, and
    (since kriya/workflow/failure.py) that it flows there as structured data
    - grade()'s likely_files assigned directly to Failure.likely_files - not
    via the old stringify-into-the-message-then-regex-re-extract round-trip."""
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.paths.logs = str(tmp_path / "logs")
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",                    # Planner
        "Design: Write app.py and helper.py",     # Architect
        "Review: Approved",                       # Reviewer
    ])

    we = WorkflowEngine(kernel, llm)
    we.developer.run_generation = AsyncMock(side_effect=[
        [
            {"filepath": "app.py", "content": "import helper\nprint('[SUCCESS]' if helper.check() else 'nope')\n"},
            {"filepath": "helper.py", "content": "def check():\n    return False\n"},
        ],
        [{"filepath": "helper.py", "content": "def check():\n    return True\n"}],
    ])
    we.run_verifier.judge = AsyncMock(return_value={
        "should_run": True,
        "run_commands": [[sys.executable, "app.py"]],
        "command_source": "goal_explicit",
        "success_criteria": "Output contains [SUCCESS]",
    })
    we.run_verifier.grade = AsyncMock(side_effect=[
        {"passed": False, "reasoning": "Missing expected marker.", "likely_files": ["helper.py"]},
        {"passed": True, "reasoning": "Output contains the expected [SUCCESS] line."},
    ])

    res = await we.run_generation_workflow(
        goal="Run with python app.py; it should print [SUCCESS]",
        workspace_path=str(tmp_path)
    )

    assert res["quality_gates_passed"] is True
    assert we.run_verifier.grade.call_count == 2
    second_call_kwargs = we.developer.run_generation.call_args_list[1].kwargs
    # "app.py" may also match independently (it's coincidentally echoed in the
    # captured output's run-command line) - what matters here is that the
    # grader's likely_files made it through at all, not exclusivity.
    assert "helper.py" in second_call_kwargs["implicated_files"]

    db_path = os.path.join(cfg.paths.logs, "traces.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM runs ORDER BY timestamp DESC LIMIT 1").fetchone()
    conn.close()
    gate_outcomes = json.loads(row["gate_outcomes"])
    run_verification_failures = [o for o in gate_outcomes if o.get("type") == "run_verification" and not o["success"]]
    assert len(run_verification_failures) == 1, gate_outcomes
    # As above: "app.py" may also be present (coincidentally echoed in the
    # captured output) - what matters is that grade()'s likely_files reached
    # the persisted gate_outcome directly, not exclusivity.
    assert "helper.py" in run_verification_failures[0]["likely_files"]

@pytest.mark.asyncio
async def test_workflow_run_verification_timeout_grades_captured_output_as_succeeded_then_hung(tmp_path):
    """Regression test for the non-binary Runtime Verification grading fix:
    _run_cmd_with_timeout still captures whatever stdout/stderr a process
    produced before being killed - previously the timeout branch never
    looked at that captured output at all, short-circuiting straight to a
    flat "Run timed out" message regardless of whether the goal's described
    behavior had already genuinely happened (the real Ignite/Qpid bug this
    generalizes: correct final output printed, then an unclosed resource's
    background threads kept the process alive). Confirms grade() is now
    called WITH the real captured output and timed_out=True even on a
    timeout, and that a grade()-confirmed success still counts as an overall
    failure (a hang is always disqualifying) but is categorized distinctly
    (type="run_verification_hung") with a message pointing at the resource
    lifecycle, not application logic."""
    cfg = AppConfig()
    cfg.paths.logs = str(tmp_path / "logs")
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write app.py",
        "Review: Approved",
    ])
    we = WorkflowEngine(kernel, llm)
    we.developer.run_generation = AsyncMock(return_value=[
        {"filepath": "app.py", "content": "print('[SUCCESS] it worked')\n"}
    ])
    we.run_verifier.judge = AsyncMock(return_value={
        "should_run": True,
        "run_commands": [[sys.executable, "app.py"]],
        "command_source": "goal_explicit",
        "success_criteria": "Output contains [SUCCESS]",
    })
    we.run_verifier.grade = AsyncMock(return_value={
        "passed": True,
        "reasoning": "The [SUCCESS] line is fully present in the captured output.",
    })
    with patch("kriya.tools.validate.PolymorphicValidator.run_app_sequence") as mock_run_seq:
        mock_run_seq.return_value = {
            "success": False,
            "timed_out": True,
            "returncode": -1,
            "output": "[SUCCESS] it worked\n",
        }
        res = await we.run_generation_workflow(
            goal="Run with python app.py; it should print [SUCCESS]",
            workspace_path=str(tmp_path),
        )

    # A hang is always disqualifying, regardless of grade()'s verdict on the
    # captured output.
    assert res["quality_gates_passed"] is False
    first_grade_call_kwargs = we.run_verifier.grade.call_args_list[0].kwargs
    assert first_grade_call_kwargs["timed_out"] is True
    assert first_grade_call_kwargs["output"] == "[SUCCESS] it worked\n"

    # The retry prompt must carry the resource-lifecycle framing, not a bare
    # "timed out" message with no actionable signal.
    second_call_kwargs = we.developer.run_generation.call_args_list[1].kwargs
    assert "never exited on its own" in second_call_kwargs["task_description"]
    assert "Fix the resource lifecycle" in second_call_kwargs["task_description"]

    db_path = os.path.join(cfg.paths.logs, "traces.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM runs ORDER BY timestamp DESC LIMIT 1").fetchone()
    conn.close()
    gate_outcomes = json.loads(row["gate_outcomes"])
    hung_failures = [o for o in gate_outcomes if o.get("type") == "run_verification_hung"]
    assert len(hung_failures) >= 1, gate_outcomes

@pytest.mark.asyncio
async def test_workflow_run_verification_timeout_with_genuine_failure_stays_plain_category(tmp_path):
    """The complementary case: a timeout where the captured output does NOT
    show the goal was achieved either must stay categorized as plain
    "run_verification" (not "_hung") - the hang-specific resource-lifecycle
    framing/category is only for a genuinely non-binary outcome, not every
    timeout."""
    cfg = AppConfig()
    cfg.paths.logs = str(tmp_path / "logs")
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write app.py",
        "Review: Approved",
    ])
    we = WorkflowEngine(kernel, llm)
    we.developer.run_generation = AsyncMock(return_value=[
        {"filepath": "app.py", "content": "print('still working...')\n"}
    ])
    we.run_verifier.judge = AsyncMock(return_value={
        "should_run": True,
        "run_commands": [[sys.executable, "app.py"]],
        "command_source": "goal_explicit",
        "success_criteria": "Output contains [SUCCESS]",
    })
    we.run_verifier.grade = AsyncMock(return_value={
        "passed": False,
        "reasoning": "The [SUCCESS] marker never appears in the captured output.",
    })
    with patch("kriya.tools.validate.PolymorphicValidator.run_app_sequence") as mock_run_seq:
        mock_run_seq.return_value = {
            "success": False,
            "timed_out": True,
            "returncode": -1,
            "output": "still working...\n",
        }
        res = await we.run_generation_workflow(
            goal="Run with python app.py; it should print [SUCCESS]",
            workspace_path=str(tmp_path),
        )

    assert res["quality_gates_passed"] is False
    db_path = os.path.join(cfg.paths.logs, "traces.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM runs ORDER BY timestamp DESC LIMIT 1").fetchone()
    conn.close()
    gate_outcomes = json.loads(row["gate_outcomes"])
    assert not [o for o in gate_outcomes if o.get("type") == "run_verification_hung"]
    plain_failures = [o for o in gate_outcomes if o.get("type") == "run_verification"]
    assert len(plain_failures) >= 1, gate_outcomes

@pytest.mark.asyncio
async def test_workflow_run_verification_substitutes_unresolvable_python(tmp_path):
    """Reproduces a real observed failure: the Runtime Verification judge inferred a
    bare 'python' run command, which isn't on PATH on many real systems (Homebrew
    installs, Debian/Ubuntu without python-is-python3) - subprocess.run raised
    FileNotFoundError immediately, failing all retry attempts regardless of whether
    the generated code was actually correct. Kriya's own interpreter must be
    substituted so the run gets a real chance to prove the code works."""
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    file_content_response = "print('[SUCCESS] it worked')\n"
    judge_response = json.dumps({
        "should_run": True,
        "run_commands": [["python", "app.py"]],  # the real observed inference - not sys.executable
        "command_source": "goal_explicit",
        "success_criteria": "Output contains [SUCCESS]"
    })
    grade_response = json.dumps({"passed": True, "reasoning": "Output contains the expected [SUCCESS] line."})

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code", "Design: Write app.py", file_content_response,
        judge_response, grade_response, "Review: Approved"
    ])

    we = WorkflowEngine(kernel, llm)

    with patch("shutil.which", return_value=None):  # simulates no bare 'python' on PATH
        res = await we.run_generation_workflow(
            goal="Run with python app.py; it should print [SUCCESS]",
            workspace_path=str(tmp_path)
        )

    # If the substitution hadn't happened, this would fail with FileNotFoundError on
    # every retry attempt and quality_gates_passed would be False.
    assert res["quality_gates_passed"] is True
    assert "app.py" in res["files"]


@pytest.mark.asyncio
async def test_workflow_run_verification_declined_still_passes_on_compile_alone(tmp_path):
    """Declining the judgment-triggered confirmation must only skip the run-verification
    step itself, not fail the whole generation - compile passing is still a valid pass.
    The same approval_callback also gates the (separate) pre-apply diff approval, which
    must still be asked and approved independently."""
    cfg = AppConfig()
    cfg.autonomy.mode = "human-in-the-loop"
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    file_content_response = "print('hello')\n"
    judge_response = json.dumps({
        "should_run": True,
        "run_commands": [[sys.executable, "app.py"]],
        "command_source": "inferred",
        "success_criteria": "Output contains hello"
    })

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",     # Planner
        "Design: Write app.py",   # Architect
        file_content_response,    # Developer
        judge_response,           # RunVerifier.judge (grade must NOT be called after this)
        "Review: Approved"        # Reviewer
    ])

    def approval_cb(files, reason):
        # Run-verification's own confirmation is called with an empty diff list;
        # the pre-apply diff-approval gate is called with the real diffs. Decline
        # only the former to isolate what's being tested.
        return bool(files)

    we = WorkflowEngine(kernel, llm)

    res = await we.run_generation_workflow(
        goal="Create a small script",
        workspace_path=str(tmp_path),
        approval_callback=approval_cb,
    )

    assert res["quality_gates_passed"] is True
    assert "app.py" in res["files"]
    # llm.complete's side_effect list has exactly 5 entries - if grade() had been
    # called despite the decline, AsyncMock would have raised StopAsyncIteration.
    assert llm.complete.await_count == 5


@pytest.mark.asyncio
async def test_workflow_full_regression_check_tests_the_applied_change_not_stale_worktree(tmp_path):
    """The full regression check runs after the worktree sandbox has already been
    git-clean'd back to its pre-change HEAD state (once files are copied out to the
    real workspace). It must test the real workspace - which has the just-applied
    change - not the now-reverted worktree, or it silently reports a false pass based
    on stale, pre-change content.

    Reproduces this with a real git repo: the committed (pre-change) calc.py has a
    deliberately WRONG add() implementation that test_calc.py's existing, untouched
    assertion would fail against. The Developer Agent "fixes" calc.py in this run. If
    the regression check were still testing the reverted worktree (the bug), it would
    run test_calc.py against the wrong, pre-change calc.py and fail; testing the real
    workspace (the fix) runs it against the corrected calc.py and passes.
    """
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a - b\n")  # deliberately wrong
    (tmp_path / "test_calc.py").write_text("from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial (buggy) commit"], cwd=tmp_path, check=True)

    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Fix calc.py",                # Planner
        "Design: Fix add() in calc.py",        # Architect
        # design's "calc.py" mention activates known_target_files on attempt 1,
        # skipping straight to a plain per-file content completion.
        "def add(a, b):\n    return a + b\n",  # Developer
        "Review: Approved"                     # Reviewer
    ])

    we = WorkflowEngine(kernel, llm)

    res = await we.run_generation_workflow(
        goal="Fix the add function in calc.py",
        workspace_path=str(tmp_path)
    )

    assert res["quality_gates_passed"] is True
    with open(tmp_path / "calc.py") as f:
        assert "a + b" in f.read()


@pytest.mark.asyncio
async def test_workflow_passing_run_verification_marks_active_skill_verified(tmp_path):
    """A passing Runtime Verification Gate run is exactly the proof the skill-gap
    check needs - any skill that contributed to a run whose generated app actually
    ran and did what the goal asked should come out of that run marked verified,
    so future runs stop asking about it."""
    from kriya.skills.skill import SkillEngine

    skills_dir = tmp_path / "skills"
    skill_folder = skills_dir / "widgetlib"
    skill_folder.mkdir(parents=True)
    (skill_folder / "skill.yaml").write_text("name: widgetlib\ndescription: Test\ntags: [widgetlib]\n")
    (skill_folder / "rules.txt").write_text("Widgets must be printed in uppercase.\n")

    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.paths.skills = str(skills_dir)
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    file_content_response = "print('[SUCCESS] WIDGET')\n"
    judge_response = json.dumps({
        "should_run": True,
        "run_commands": [[sys.executable, "app.py"]],
        "command_source": "goal_explicit",
        "success_criteria": "Output contains [SUCCESS]"
    })
    grade_response = json.dumps({"passed": True, "reasoning": "Output contains the expected line."})

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",     # Planner
        "Design: Write app.py",   # Architect
        file_content_response,    # Developer
        judge_response,           # RunVerifier.judge
        grade_response,           # RunVerifier.grade
        "Review: Approved"        # Reviewer
    ])

    we = WorkflowEngine(kernel, llm)

    res = await we.run_generation_workflow(
        goal="Run with python app.py; the widgetlib==2.0 skill applies here",
        workspace_path=str(tmp_path)
    )

    assert res["quality_gates_passed"] is True

    se = SkillEngine(str(skills_dir), load_global=False)
    se.discover_and_load()
    skill = se.get_skill("widgetlib")
    assert skill.verified is True
    assert skill.verified_context == "widgetlib 2.0.0"
    assert skill.verified_at is not None

@pytest.mark.asyncio
async def test_workflow_extracted_rule_unverified_then_promoted_by_passing_run(tmp_path):
    """A freshly extracted rule must be labeled distinctly as unverified in the SAME
    run's generation prompt (not blended in as equally authoritative as long-standing
    rules), and a passing Runtime Verification run must promote exactly that rule -
    not the skill's pre-existing untracked content, which was never flagged in the
    first place - to verified in the per-rule provenance file."""
    from kriya.skills.skill import SkillEngine, load_rule_provenance

    skills_dir = tmp_path / "skills"
    skill_folder = skills_dir / "widgetlib"
    skill_folder.mkdir(parents=True)
    (skill_folder / "skill.yaml").write_text("name: widgetlib\ndescription: Test\ntags: [widgetlib]\n")
    (skill_folder / "rules.txt").write_text("Existing rule.\n")

    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.paths.skills = str(skills_dir)
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    extraction_response = json.dumps({
        "rules": ["The magic widget constant is 42."], "examples": {}, "conflicts": []
    })
    file_content_response = "print('[SUCCESS] WIDGET')\n"
    judge_response = json.dumps({
        "should_run": True,
        "run_commands": [[sys.executable, "app.py"]],
        "command_source": "goal_explicit",
        "success_criteria": "Output contains [SUCCESS]"
    })
    grade_response = json.dumps({"passed": True, "reasoning": "Output contains the expected line."})

    llm.complete = AsyncMock(side_effect=[
        extraction_response,   # SkillGapAgent.extract_skill_update for the human-supplied text
        "Step 1: Write code",  # Planner
        "Design: Write app.py",  # Architect
        file_content_response, # Developer
        judge_response,         # RunVerifier.judge
        grade_response,         # RunVerifier.grade
        "Review: Approved"      # Reviewer
    ])

    def skill_gap_cb(reason, names):
        return "The magic widget constant is 42."

    we = WorkflowEngine(kernel, llm)
    res = await we.run_generation_workflow(
        goal="Run with python app.py; the widgetlib skill applies here",
        workspace_path=str(tmp_path),
        skill_gap_callback=skill_gap_cb,
    )

    assert res["quality_gates_passed"] is True

    # The freshly-extracted rule was labeled unverified in the prompt sent to Planner.
    planner_prompt = llm.complete.call_args_list[1].args[1]
    assert "Unverified Rules:\n- The magic widget constant is 42." in planner_prompt

    se = SkillEngine(str(skills_dir), load_global=False)
    se.discover_and_load()
    skill = se.get_skill("widgetlib")
    assert skill.verified is True  # skill-level flag, unchanged existing behavior

    provenance = {p["text"]: p for p in load_rule_provenance(skill.source_path)}
    assert provenance["The magic widget constant is 42."]["verified"] is True
    # The pre-existing, never-tracked rule has no provenance record at all - it was
    # never flagged unverified, so there's nothing for a passing run to "promote".
    assert "Existing rule." not in provenance


@pytest.mark.asyncio
async def test_workflow_surfaces_skill_staleness_warning_on_version_drift(tmp_path):
    """Architectural add-on from a 2026-08-12 SME review: a skill's
    verified_context already records what version it was verified against,
    but nothing ever read it back to check against a later goal's mentioned
    version. Confirms the end-to-end wiring: a skill verified against Ignite
    2.18.0, activated for a goal mentioning Ignite 2.20.0, surfaces a
    staleness warning in the result dict - not just at the unit level."""
    _init_git_repo(tmp_path)
    skills_dir = tmp_path / "skills"
    skill_folder = skills_dir / "ignite-java17"
    skill_folder.mkdir(parents=True)
    (skill_folder / "skill.yaml").write_text(
        "name: ignite-java17\n"
        "description: Ignite skill\n"
        "tags: [ignite]\n"
        "verified: true\n"
        "verified_at: '2026-08-01'\n"
        "verified_context: 'org.apache.ignite:ignite-core 2.18.0'\n"
    )

    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.paths.skills = str(skills_dir)
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write app.py",
        "print('hi')\n",
        "Review: Approved",
    ])

    we = WorkflowEngine(kernel, llm)
    from kriya.tools.knowledge import GapReport
    with patch("kriya.tools.knowledge.KnowledgeGuard.check_goal", return_value=GapReport()):
        res = await we.run_generation_workflow(
            goal="Use Apache Ignite 2.20.0 for caching in this app",
            workspace_path=str(tmp_path),
        )

    assert res["quality_gates_passed"] is True
    assert res["skill_staleness_warnings"] is not None
    warning = res["skill_staleness_warnings"][0]
    assert "ignite-java17" in warning
    assert "2.18.0" in warning
    assert "2.20.0" in warning


@pytest.mark.asyncio
async def test_workflow_skill_gap_refuses_url_fetch_under_local_only_egress(tmp_path):
    """A supplied URL must not be fetched when autonomy.egress_policy is local_only -
    that's a new outbound-network capability this feature adds, and it should get the
    same guarantee the rest of Kriya already gives. File/text answers are unaffected -
    only the URL branch is gated."""
    from kriya.skills.skill import SkillEngine

    skills_dir = tmp_path / "skills"
    skill_folder = skills_dir / "widgetlib"
    skill_folder.mkdir(parents=True)
    (skill_folder / "skill.yaml").write_text("name: widgetlib\ndescription: Test\ntags: [widgetlib]\n")
    (skill_folder / "rules.txt").write_text("Existing rule.\n")

    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.paths.skills = str(skills_dir)
    cfg.autonomy.run_verification_enabled = False
    cfg.autonomy.egress_policy = "local_only"
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",     # Planner
        "Design: Write app.py",   # Architect
        '[{"filepath": "app.py", "content": "print(1)\\n"}]',  # Developer
        "Review: Approved"        # Reviewer
    ])

    def skill_gap_cb(reason, names):
        return "https://example.com/widgetlib-docs"

    we = WorkflowEngine(kernel, llm)

    with patch("kriya.tools.web.fetch_url_text", new_callable=AsyncMock) as mock_fetch:
        res = await we.run_generation_workflow(
            goal="Build something with the widgetlib skill",
            workspace_path=str(tmp_path),
            skill_gap_callback=skill_gap_cb,
        )
        mock_fetch.assert_not_called()

    assert res["quality_gates_passed"] is True
    se = SkillEngine(str(skills_dir), load_global=False)
    se.discover_and_load()
    skill = se.get_skill("widgetlib")
    assert skill.verified is False
    assert skill.rules == ["Existing rule."]


@pytest.mark.asyncio
async def test_workflow_skill_gap_decline_marks_acknowledged_and_proceeds(tmp_path):
    """Declining the skill-gap ask (callback returns None) must not fail generation -
    it proceeds with the skill as-is, same as today's baseline - and must remember the
    decline so future runs don't keep re-asking about the same skill."""
    from kriya.skills.skill import SkillEngine

    skills_dir = tmp_path / "skills"
    skill_folder = skills_dir / "widgetlib"
    skill_folder.mkdir(parents=True)
    (skill_folder / "skill.yaml").write_text("name: widgetlib\ndescription: Test\ntags: [widgetlib]\n")
    (skill_folder / "rules.txt").write_text("Existing rule.\n")

    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.paths.skills = str(skills_dir)
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write app.py",
        '[{"filepath": "app.py", "content": "print(1)\\n"}]',
        "Review: Approved"
    ])

    calls = []
    def skill_gap_cb(reason, names):
        calls.append(names)
        return None

    we = WorkflowEngine(kernel, llm)
    res = await we.run_generation_workflow(
        goal="Build something with the widgetlib skill",
        workspace_path=str(tmp_path),
        skill_gap_callback=skill_gap_cb,
    )

    assert res["quality_gates_passed"] is True
    assert calls == [["widgetlib"]]

    se = SkillEngine(str(skills_dir), load_global=False)
    se.discover_and_load()
    assert se.get_skill("widgetlib").verification_gap_acknowledged is True
    # Regression test for a real, previously-invisible gap: skill-gap detection
    # and the decline path both correctly happen, but nothing downstream ever
    # connected a resulting run - pass or fail - back to "this proceeded on an
    # unresolved knowledge gap." A human explicitly acknowledging "proceed
    # without it" is still a genuinely unresolved gap for the code that gets
    # generated, distinct from a real fact being added.
    assert res["unresolved_skill_gaps"] == ["widgetlib"]

@pytest.mark.asyncio
async def test_workflow_no_skill_gap_callback_still_reports_unresolved_gap(tmp_path):
    """A gap exists but no skill_gap_callback was even wired (e.g. as `fix`
    doesn't wire one) - the gap is never offered a chance to resolve at all,
    which must still surface in the final report, not disappear silently."""
    from kriya.skills.skill import SkillEngine

    skills_dir = tmp_path / "skills"
    skill_folder = skills_dir / "widgetlib"
    skill_folder.mkdir(parents=True)
    (skill_folder / "skill.yaml").write_text("name: widgetlib\ndescription: Test\ntags: [widgetlib]\n")
    (skill_folder / "rules.txt").write_text("Existing rule.\n")

    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.paths.skills = str(skills_dir)
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write app.py",
        '[{"filepath": "app.py", "content": "print(1)\\n"}]',
        "Review: Approved"
    ])

    we = WorkflowEngine(kernel, llm)
    res = await we.run_generation_workflow(
        goal="Build something with the widgetlib skill",
        workspace_path=str(tmp_path),
        # No skill_gap_callback passed at all.
    )

    assert res["quality_gates_passed"] is True
    assert res["unresolved_skill_gaps"] == ["widgetlib"]

    se = SkillEngine(str(skills_dir), load_global=False)
    se.discover_and_load()
    # No callback means the skill was never even asked about - must NOT be
    # silently marked acknowledged, unlike the explicit-decline case above.
    assert se.get_skill("widgetlib").verification_gap_acknowledged is False

@pytest.mark.asyncio
async def test_workflow_unresolved_skill_gaps_none_when_nothing_flagged(tmp_path):
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write app.py",
        '[{"filepath": "app.py", "content": "print(1)\\n"}]',
        "Review: Approved"
    ])

    we = WorkflowEngine(kernel, llm)
    res = await we.run_generation_workflow(
        goal="Build a simple math library",
        workspace_path=str(tmp_path),
    )

    assert res["quality_gates_passed"] is True
    assert res["unresolved_skill_gaps"] is None

@pytest.mark.asyncio
async def test_workflow_skill_gap_not_reasked_once_acknowledged(tmp_path):
    """A second run must not re-invoke the callback for a skill already acknowledged
    by a prior decline."""
    skills_dir = tmp_path / "skills"
    skill_folder = skills_dir / "widgetlib"
    skill_folder.mkdir(parents=True)
    (skill_folder / "skill.yaml").write_text(
        "name: widgetlib\ndescription: Test\ntags: [widgetlib]\nverification_gap_acknowledged: true\n"
    )
    (skill_folder / "rules.txt").write_text("Existing rule.\n")

    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.paths.skills = str(skills_dir)
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write app.py",
        '[{"filepath": "app.py", "content": "print(1)\\n"}]',
        "Review: Approved"
    ])

    calls = []
    def skill_gap_cb(reason, names):
        calls.append(names)
        return None

    we = WorkflowEngine(kernel, llm)
    res = await we.run_generation_workflow(
        goal="Build something with the widgetlib skill",
        workspace_path=str(tmp_path),
        skill_gap_callback=skill_gap_cb,
    )

    assert res["quality_gates_passed"] is True
    assert calls == []


def _make_conflicting_skills_dir(tmp_path):
    # Deliberately fictional, non-colliding skill names - the real repo's own
    # skills/ directory (qpid, activemq-artemis, ...) is also loaded alongside a
    # project-local skills dir (SkillEngine defaults load_global=True), so reusing
    # those real names here would spuriously match real skills too.
    skills_dir = tmp_path / "skills"
    (skills_dir / "brokeralpha").mkdir(parents=True)
    (skills_dir / "brokeralpha" / "skill.yaml").write_text("name: brokeralpha\ndescription: Test\ntags: [brokeralpha]\n")
    (skills_dir / "brokeralpha" / "rules.txt").write_text("Broker must bind AMQP to port 5672.\n")
    (skills_dir / "brokerbeta").mkdir(parents=True)
    (skills_dir / "brokerbeta" / "skill.yaml").write_text("name: brokerbeta\ndescription: Test\ntags: [brokerbeta]\n")
    (skills_dir / "brokerbeta" / "rules.txt").write_text("Configure the broker to listen on port 5673 for AMQP clients.\n")
    return skills_dir

_ALPHA_RULE = "Broker must bind AMQP to port 5672."
_BETA_RULE = "Configure the broker to listen on port 5673 for AMQP clients."

# active_skills is sorted alphabetically before pairwise comparison, so
# "brokeralpha" (< "brokerbeta") is always passed as skill_a/rule_a here.
_CONFLICT_RESPONSE = json.dumps({
    "conflicts": [{
        "rule_a_index": 1,
        "rule_b_index": 1,
        "explanation": "Both skills configure the same embedded broker's AMQP port to a different value."
    }]
})

@pytest.mark.asyncio
async def test_workflow_skill_conflict_excludes_losing_rule_and_persists_resolution(tmp_path):
    """Two independently valid skills can still conflict when both are active for the
    same run (e.g. two broker skills each pinning a different port for what must be a
    single shared setting). A human-resolved 'prefer_a' must exclude the losing rule
    from THIS run's context, and remember the decision for future runs."""
    from kriya.skills.skill import load_conflict_resolutions

    skills_dir = _make_conflicting_skills_dir(tmp_path)

    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.paths.skills = str(skills_dir)
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    llm.complete = AsyncMock(side_effect=[
        _CONFLICT_RESPONSE,       # SkillGapAgent.check_skill_conflicts
        "Step 1: Write code",     # Planner
        "Design: Write app.py",   # Architect
        '[{"filepath": "app.py", "content": "print(1)\\n"}]',  # Developer
        "Review: Approved"        # Reviewer
    ])

    def conflict_cb(skill_a, rule_a, skill_b, rule_b, explanation):
        return "prefer_a"  # artemis (skill_a, alphabetically first) wins

    we = WorkflowEngine(kernel, llm)
    res = await we.run_generation_workflow(
        goal="Build something using both the brokeralpha and brokerbeta skills",
        workspace_path=str(tmp_path),
        skill_conflict_callback=conflict_cb,
    )

    assert res["quality_gates_passed"] is True

    planner_prompt = llm.complete.call_args_list[1].args[1]
    assert _ALPHA_RULE in planner_prompt
    assert _BETA_RULE not in planner_prompt

    records = load_conflict_resolutions(str(skills_dir))
    assert len(records) == 1
    assert records[0]["resolution"] == "prefer_a"

@pytest.mark.asyncio
async def test_workflow_skill_conflict_remembered_resolution_skips_callback(tmp_path):
    """A conflict already resolved for this exact skill/rule pair on a prior run must
    be applied silently - the callback should not be re-invoked."""
    from kriya.skills.skill import record_conflict_resolution

    skills_dir = _make_conflicting_skills_dir(tmp_path)
    record_conflict_resolution(str(skills_dir), "brokeralpha", _ALPHA_RULE, "brokerbeta", _BETA_RULE, "prefer_b")

    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.paths.skills = str(skills_dir)
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    llm.complete = AsyncMock(side_effect=[
        _CONFLICT_RESPONSE,
        "Step 1: Write code",
        "Design: Write app.py",
        '[{"filepath": "app.py", "content": "print(1)\\n"}]',
        "Review: Approved"
    ])

    calls = []
    def conflict_cb(skill_a, rule_a, skill_b, rule_b, explanation):
        calls.append((skill_a, skill_b))
        return "prefer_a"

    we = WorkflowEngine(kernel, llm)
    res = await we.run_generation_workflow(
        goal="Build something using both the brokeralpha and brokerbeta skills",
        workspace_path=str(tmp_path),
        skill_conflict_callback=conflict_cb,
    )

    assert res["quality_gates_passed"] is True
    assert calls == []  # remembered resolution applied without asking again

    planner_prompt = llm.complete.call_args_list[1].args[1]
    # The remembered resolution was "prefer_b" (qpid wins) - qpid's rule must be the
    # one that survives, artemis's the one excluded.
    assert _BETA_RULE in planner_prompt
    assert _ALPHA_RULE not in planner_prompt

@pytest.mark.asyncio
async def test_workflow_skill_conflict_check_sees_rule_extracted_earlier_in_same_run(tmp_path):
    """Stage 5 SME review, Finding 2 (2026-08-15): section 1.3's cross-skill conflict
    check reads skill.rules from SkillEngine's in-memory cache, but section 1.2's
    extraction (skill-gap resolution) writes new rules straight to rules.txt on disk
    without refreshing that cache. Before the fix (a se.discover_and_load() reload
    right before 1.3), a rule extracted moments earlier in THIS SAME RUN was invisible
    to the conflict check that runs immediately after it - 'brokeralpha' starts
    unverified with NO rules on disk, gets _ALPHA_RULE extracted live via the
    skill-gap callback, and 'brokerbeta' already has the genuinely conflicting
    _BETA_RULE. The conflict must actually be caught in THIS run, not just a future
    one."""
    from kriya.skills.skill import load_conflict_resolutions

    skills_dir = tmp_path / "skills"
    (skills_dir / "brokeralpha").mkdir(parents=True)
    (skills_dir / "brokeralpha" / "skill.yaml").write_text("name: brokeralpha\ndescription: Test\ntags: [brokeralpha]\n")
    (skills_dir / "brokeralpha" / "rules.txt").write_text("")
    (skills_dir / "brokerbeta").mkdir(parents=True)
    (skills_dir / "brokerbeta" / "skill.yaml").write_text("name: brokerbeta\ndescription: Test\ntags: [brokerbeta]\nverified: true\n")
    (skills_dir / "brokerbeta" / "rules.txt").write_text(f"{_BETA_RULE}\n")

    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.paths.skills = str(skills_dir)
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    extraction_response = json.dumps({"rules": [_ALPHA_RULE], "examples": {}, "conflicts": []})
    llm.complete = AsyncMock(side_effect=[
        extraction_response,      # SkillGapAgent.extract_skill_update for brokeralpha's gap (1.2)
        _CONFLICT_RESPONSE,       # SkillGapAgent.check_skill_conflicts (1.3)
        "Step 1: Write code",     # Planner
        "Design: Write app.py",   # Architect
        '[{"filepath": "app.py", "content": "print(1)\\n"}]',  # Developer
        "Review: Approved"        # Reviewer
    ])

    def skill_gap_cb(reason, names):
        return "Reference material establishing the alpha broker's port rule."

    def conflict_cb(skill_a, rule_a, skill_b, rule_b, explanation):
        return "prefer_b"  # brokerbeta (pre-existing, verified) wins

    we = WorkflowEngine(kernel, llm)
    res = await we.run_generation_workflow(
        goal="Build something using both the brokeralpha and brokerbeta skills",
        workspace_path=str(tmp_path),
        skill_gap_callback=skill_gap_cb,
        skill_conflict_callback=conflict_cb,
    )

    assert res["quality_gates_passed"] is True

    # If the conflict check never fired (the stale-cache bug), both rules would appear
    # unfiltered in the Planner prompt - the whole point of section 1.3 is to prevent
    # exactly that.
    planner_prompt = llm.complete.call_args_list[2].args[1]
    assert _BETA_RULE in planner_prompt
    assert _ALPHA_RULE not in planner_prompt

    records = load_conflict_resolutions(str(skills_dir))
    assert len(records) == 1

@pytest.mark.asyncio
async def test_workflow_skill_conflict_no_callback_response_does_not_persist(tmp_path):
    """A callback that returns nothing usable (e.g. -y auto-skip, or a callback error)
    must not exclude either rule and must not write a resolution to the registry -
    only an explicit human decision should be remembered."""
    from kriya.skills.skill import load_conflict_resolutions

    skills_dir = _make_conflicting_skills_dir(tmp_path)

    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.paths.skills = str(skills_dir)
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    llm.complete = AsyncMock(side_effect=[
        _CONFLICT_RESPONSE,
        "Step 1: Write code",
        "Design: Write app.py",
        '[{"filepath": "app.py", "content": "print(1)\\n"}]',
        "Review: Approved"
    ])

    def conflict_cb(skill_a, rule_a, skill_b, rule_b, explanation):
        return None

    we = WorkflowEngine(kernel, llm)
    res = await we.run_generation_workflow(
        goal="Build something using both the brokeralpha and brokerbeta skills",
        workspace_path=str(tmp_path),
        skill_conflict_callback=conflict_cb,
    )

    assert res["quality_gates_passed"] is True

    planner_prompt = llm.complete.call_args_list[1].args[1]
    assert _ALPHA_RULE in planner_prompt
    assert _BETA_RULE in planner_prompt

    assert load_conflict_resolutions(str(skills_dir)) == []

@pytest.mark.asyncio
async def test_workflow_no_conflict_check_without_callback(tmp_path):
    """Without a skill_conflict_callback (e.g. the `fix` pipeline, which doesn't wire
    one in), the conflict-detection LLM call must not fire at all."""
    skills_dir = _make_conflicting_skills_dir(tmp_path)

    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.paths.skills = str(skills_dir)
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",     # Planner - no conflict-check call precedes it
        "Design: Write app.py",
        '[{"filepath": "app.py", "content": "print(1)\\n"}]',
        "Review: Approved"
    ])

    we = WorkflowEngine(kernel, llm)
    res = await we.run_generation_workflow(
        goal="Build something using both the brokeralpha and brokerbeta skills",
        workspace_path=str(tmp_path),
    )

    assert res["quality_gates_passed"] is True


@pytest.mark.asyncio
async def test_workflow_web_lookup_auto_resolves_skill_gap(tmp_path):
    """When web_lookup_enabled and a search backend are configured, an unverified
    skill gap should be auto-resolved via search+fetch BEFORE ever asking a human for
    a URL - the human-ask path (skill_gap_callback) must not fire at all if live
    lookup fully resolves the gap and the batch is confirmed."""
    from kriya.skills.skill import SkillEngine

    skills_dir = tmp_path / "skills"
    skill_folder = skills_dir / "widgetlib"
    skill_folder.mkdir(parents=True)
    (skill_folder / "skill.yaml").write_text("name: widgetlib\ndescription: Test\ntags: [widgetlib]\n")
    (skill_folder / "rules.txt").write_text("Existing rule.\n")

    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.paths.skills = str(skills_dir)
    cfg.autonomy.run_verification_enabled = False
    cfg.autonomy.web_lookup_enabled = True
    cfg.autonomy.web_lookup_auto_approve = True  # bypass the pre-send confirmation gate for this test
    cfg.search.base_url = "http://fake-search:8080"
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    extraction_response = json.dumps({
        "rules": ["The magic widget constant is 42."],
        "examples": {},
        "conflicts": []
    })
    llm.complete = AsyncMock(side_effect=[
        extraction_response,      # SkillGapAgent.extract_skill_update, for the auto-found reference
        "Step 1: Write code",     # Planner
        "Design: Write app.py",   # Architect
        '[{"filepath": "app.py", "content": "print(1)\\n"}]',  # Developer
        "Review: Approved"        # Reviewer
    ])

    found_result = [{"term": "widgetlib", "title": "Widgetlib Docs", "url": "https://example.com/widgetlib", "snippet": "..."}]
    skill_gap_calls = []
    def skill_gap_cb(reason, names):
        skill_gap_calls.append(names)
        return None

    we = WorkflowEngine(kernel, llm)
    with patch("kriya.tools.search.search_web", new=AsyncMock(return_value=found_result)) as mock_search, \
         patch("kriya.tools.web.fetch_url_text", new=AsyncMock(return_value="The magic widget constant is 42.")) as mock_fetch:
        res = await we.run_generation_workflow(
            goal="Build something with the widgetlib skill",
            workspace_path=str(tmp_path),
            skill_gap_callback=skill_gap_cb,
            web_lookup_callback=lambda found: True,
        )

    assert res["quality_gates_passed"] is True
    mock_search.assert_called_once_with("widgetlib example", "http://fake-search:8080", top_k=3)
    mock_fetch.assert_called_once_with("https://example.com/widgetlib", quiet_on_failure=True)
    assert skill_gap_calls == []  # human-ask path never fired - live lookup resolved it first

    se = SkillEngine(str(skills_dir), load_global=False)
    se.discover_and_load()
    skill = se.get_skill("widgetlib")
    assert "The magic widget constant is 42." in skill.rules

@pytest.mark.asyncio
async def test_workflow_web_lookup_never_fires_without_approval(tmp_path):
    """Regression test: before the pre-send confirmation gate existed, a
    non-interactive (-y) run fired the outbound live-lookup query and then
    silently discarded the result under web_lookup_callback's own -y auto-
    decline - the query had already left the machine with zero human
    visibility, for zero benefit. Now, with neither web_lookup_auto_approve
    set nor a web_lookup_query_callback supplied, the search must never fire
    at all, and must fall through to the human-ask path exactly as if lookup
    had been tried and found nothing."""
    skills_dir = tmp_path / "skills"
    skill_folder = skills_dir / "widgetlib"
    skill_folder.mkdir(parents=True)
    (skill_folder / "skill.yaml").write_text("name: widgetlib\ndescription: Test\ntags: [widgetlib]\n")
    (skill_folder / "rules.txt").write_text("Existing rule.\n")

    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.paths.skills = str(skills_dir)
    cfg.autonomy.run_verification_enabled = False
    cfg.autonomy.web_lookup_enabled = True
    cfg.search.base_url = "http://fake-search:8080"
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write app.py",
        '[{"filepath": "app.py", "content": "print(1)\\n"}]',
        "Review: Approved",
    ])

    skill_gap_calls = []
    def skill_gap_cb(reason, names):
        skill_gap_calls.append(names)
        return None

    we = WorkflowEngine(kernel, llm)
    with patch("kriya.tools.search.search_web", new=AsyncMock()) as mock_search:
        res = await we.run_generation_workflow(
            goal="Build something with the widgetlib skill",
            workspace_path=str(tmp_path),
            skill_gap_callback=skill_gap_cb,
            # No web_lookup_query_callback passed - must fail closed.
        )

    assert res["quality_gates_passed"] is True
    mock_search.assert_not_called()
    assert skill_gap_calls and "widgetlib" in skill_gap_calls[0]

@pytest.mark.asyncio
async def test_workflow_web_lookup_query_callback_can_approve(tmp_path):
    """The real-time callback path (not just the auto_approve config flag) must
    also be able to authorize a search, and must receive the exact bare terms
    and base_url about to be sent - never goal/design/code text."""
    from kriya.skills.skill import SkillEngine

    skills_dir = tmp_path / "skills"
    skill_folder = skills_dir / "widgetlib"
    skill_folder.mkdir(parents=True)
    (skill_folder / "skill.yaml").write_text("name: widgetlib\ndescription: Test\ntags: [widgetlib]\n")
    (skill_folder / "rules.txt").write_text("Existing rule.\n")

    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.paths.skills = str(skills_dir)
    cfg.autonomy.run_verification_enabled = False
    cfg.autonomy.web_lookup_enabled = True
    cfg.search.base_url = "http://fake-search:8080"
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    extraction_response = json.dumps({
        "rules": ["The magic widget constant is 42."],
        "examples": {},
        "conflicts": []
    })
    llm.complete = AsyncMock(side_effect=[
        extraction_response,
        "Step 1: Write code",
        "Design: Write app.py",
        '[{"filepath": "app.py", "content": "print(1)\\n"}]',
        "Review: Approved"
    ])

    found_result = [{"term": "widgetlib", "title": "Widgetlib Docs", "url": "https://example.com/widgetlib", "snippet": "..."}]
    query_calls = []
    def query_cb(terms, base_url):
        query_calls.append((terms, base_url))
        return True

    we = WorkflowEngine(kernel, llm)
    with patch("kriya.tools.search.search_web", new=AsyncMock(return_value=found_result)) as mock_search, \
         patch("kriya.tools.web.fetch_url_text", new=AsyncMock(return_value="The magic widget constant is 42.")):
        res = await we.run_generation_workflow(
            goal="Build something with the widgetlib skill",
            workspace_path=str(tmp_path),
            skill_gap_callback=lambda reason, names: None,
            web_lookup_callback=lambda found: True,
            web_lookup_query_callback=query_cb,
        )

    assert res["quality_gates_passed"] is True
    mock_search.assert_called_once()
    assert query_calls == [(["widgetlib"], "http://fake-search:8080")]

    se = SkillEngine(str(skills_dir), load_global=False)
    se.discover_and_load()
    assert "The magic widget constant is 42." in se.get_skill("widgetlib").rules

@pytest.mark.asyncio
async def test_workflow_retry_loop_live_lookup_never_fires_without_approval(tmp_path):
    """The retry loop's error-triggered live lookup (Stage 2C) must respect the
    same pre-send gate as the goal/design-stage lookups - it was previously the
    one path with NO confirmation of any kind, deliberately designed as fully
    unattended, but that also meant it fired without ever needing authorization."""
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.autonomy.web_lookup_enabled = True
    cfg.search.base_url = "http://fake-search:8080"
    cfg.paths.skills = str(tmp_path / "skills")
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    repeated_error = (
        "Failed to execute goal org.codehaus.mojo:exec-maven-plugin:3.1.0:java "
        "for parameter arguments: Cannot store value into array"
    )

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write App.java",
        '[{"filepath": "App.java", "content": "class App {}"}]',
        '[{"filepath": "App.java", "content": "class App {}"}]',
        '[{"filepath": "App.java", "content": "class App {}"}]',
        "Two full-set retries were needed - lesson extraction.",
        "Review: Approved",
    ])

    we = WorkflowEngine(kernel, llm)
    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check") as mock_compile, \
         patch("kriya.tools.search.search_web", new=AsyncMock()) as mock_search:
        mock_compile.side_effect = [
            {"success": False, "output": repeated_error},
            {"success": False, "output": repeated_error},
            {"success": True, "output": ""},
        ]
        res = await we.run_generation_workflow(
            goal="Create a Java app", workspace_path=str(tmp_path)
            # No web_lookup_query_callback, no auto_approve - must fail closed
            # even mid-retry-loop.
        )

    assert res["quality_gates_passed"] is True
    mock_search.assert_not_called()

@pytest.mark.asyncio
async def test_workflow_web_lookup_falls_through_to_next_candidate_on_empty_extraction(tmp_path):
    """A single unhelpful top search result (a landing page with nothing concrete to
    extract - confirmed to happen in real testing) must not sink the whole lookup:
    the next candidate should be tried, and the term only counts as unresolved if
    NONE of the fetched candidates yield anything usable."""
    from kriya.skills.skill import SkillEngine

    skills_dir = tmp_path / "skills"
    skill_folder = skills_dir / "widgetlib"
    skill_folder.mkdir(parents=True)
    (skill_folder / "skill.yaml").write_text("name: widgetlib\ndescription: Test\ntags: [widgetlib]\n")
    (skill_folder / "rules.txt").write_text("Existing rule.\n")

    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.paths.skills = str(skills_dir)
    cfg.autonomy.run_verification_enabled = False
    cfg.autonomy.web_lookup_enabled = True
    cfg.autonomy.web_lookup_auto_approve = True  # bypass the pre-send confirmation gate for this test
    cfg.search.base_url = "http://fake-search:8080"
    cfg.search.top_k = 2
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    empty_extraction = json.dumps({"rules": [], "examples": {}, "conflicts": []})
    real_extraction = json.dumps({"rules": ["The magic widget constant is 42."], "examples": {}, "conflicts": []})
    llm.complete = AsyncMock(side_effect=[
        empty_extraction,         # candidate 1 (landing page) - nothing usable
        real_extraction,          # candidate 2 - the real answer
        "Step 1: Write code",     # Planner
        "Design: Write app.py",   # Architect
        '[{"filepath": "app.py", "content": "print(1)\\n"}]',  # Developer
        "Review: Approved"        # Reviewer
    ])

    found_result = [
        {"title": "Widgetlib Home", "url": "https://example.com/widgetlib-home", "snippet": "landing page"},
        {"title": "Widgetlib Reference", "url": "https://example.com/widgetlib-ref", "snippet": "reference docs"},
    ]

    async def fetch_side_effect(url, quiet_on_failure=False):
        return "Marketing copy, no specifics." if url.endswith("-home") else "The magic widget constant is 42."

    we = WorkflowEngine(kernel, llm)
    with patch("kriya.tools.search.search_web", new=AsyncMock(return_value=found_result)), \
         patch("kriya.tools.web.fetch_url_text", new=AsyncMock(side_effect=fetch_side_effect)) as mock_fetch:
        res = await we.run_generation_workflow(
            goal="Build something with the widgetlib skill",
            workspace_path=str(tmp_path),
            skill_gap_callback=lambda reason, names: None,
            web_lookup_callback=lambda found: True,
        )

    assert res["quality_gates_passed"] is True
    assert mock_fetch.await_count == 2  # both candidates were fetched

    se = SkillEngine(str(skills_dir), load_global=False)
    se.discover_and_load()
    skill = se.get_skill("widgetlib")
    assert "The magic widget constant is 42." in skill.rules

@pytest.mark.asyncio
async def test_workflow_web_lookup_declined_falls_back_to_human_ask(tmp_path):
    """Declining the live-lookup batch confirmation must fall back to the existing
    human-ask (skill_gap_callback) path, not silently drop the gap."""
    skills_dir = tmp_path / "skills"
    skill_folder = skills_dir / "widgetlib"
    skill_folder.mkdir(parents=True)
    (skill_folder / "skill.yaml").write_text("name: widgetlib\ndescription: Test\ntags: [widgetlib]\n")
    (skill_folder / "rules.txt").write_text("Existing rule.\n")

    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.paths.skills = str(skills_dir)
    cfg.autonomy.run_verification_enabled = False
    cfg.autonomy.web_lookup_enabled = True
    cfg.autonomy.web_lookup_auto_approve = True  # bypass the pre-send confirmation gate for this test
    cfg.search.base_url = "http://fake-search:8080"
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write app.py",
        '[{"filepath": "app.py", "content": "print(1)\\n"}]',
        "Review: Approved"
    ])

    found_result = [{"term": "widgetlib", "title": "Widgetlib Docs", "url": "https://example.com/widgetlib", "snippet": "..."}]
    skill_gap_calls = []
    def skill_gap_cb(reason, names):
        skill_gap_calls.append(names)
        return None

    we = WorkflowEngine(kernel, llm)
    with patch("kriya.tools.search.search_web", new=AsyncMock(return_value=found_result)), \
         patch("kriya.tools.web.fetch_url_text", new=AsyncMock(return_value="text")):
        res = await we.run_generation_workflow(
            goal="Build something with the widgetlib skill",
            workspace_path=str(tmp_path),
            skill_gap_callback=skill_gap_cb,
            web_lookup_callback=lambda found: False,  # decline the batch
        )

    assert res["quality_gates_passed"] is True
    assert skill_gap_calls == [["widgetlib"]]  # fell back to asking a human

@pytest.mark.asyncio
async def test_workflow_web_lookup_accepted_but_empty_falls_back_to_human_ask(tmp_path):
    """Bug fix: accepting the live-lookup batch doesn't by itself mean the gap is
    resolved - if extraction across all fetched candidates still comes up empty, the
    term must fall through to skill_gap_callback exactly as if lookup had never run,
    not silently leave the skill unverified with no one ever asked. If the human then
    supplies something usable, it must be applied."""
    from kriya.skills.skill import SkillEngine

    skills_dir = tmp_path / "skills"
    skill_folder = skills_dir / "widgetlib"
    skill_folder.mkdir(parents=True)
    (skill_folder / "skill.yaml").write_text("name: widgetlib\ndescription: Test\ntags: [widgetlib]\n")
    (skill_folder / "rules.txt").write_text("Existing rule.\n")

    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.paths.skills = str(skills_dir)
    cfg.autonomy.run_verification_enabled = False
    cfg.autonomy.web_lookup_enabled = True
    cfg.autonomy.web_lookup_auto_approve = True  # bypass the pre-send confirmation gate for this test
    cfg.search.base_url = "http://fake-search:8080"
    cfg.search.top_k = 1
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    empty_extraction = json.dumps({"rules": [], "examples": {}, "conflicts": []})
    real_extraction = json.dumps({"rules": ["The magic widget constant is 42."], "examples": {}, "conflicts": []})
    llm.complete = AsyncMock(side_effect=[
        empty_extraction,         # live-lookup's one candidate - nothing usable
        real_extraction,          # human-supplied reference, after fallback - usable
        "Step 1: Write code",     # Planner
        "Design: Write app.py",   # Architect
        '[{"filepath": "app.py", "content": "print(1)\\n"}]',  # Developer
        "Review: Approved"        # Reviewer
    ])

    found_result = [{"title": "Widgetlib Landing Page", "url": "https://example.com/widgetlib", "snippet": "..."}]
    skill_gap_calls = []
    def skill_gap_cb(reason, names):
        skill_gap_calls.append(names)
        return "The magic widget constant is 42."  # pasted text, not a URL - avoids egress_policy entirely

    we = WorkflowEngine(kernel, llm)
    with patch("kriya.tools.search.search_web", new=AsyncMock(return_value=found_result)), \
         patch("kriya.tools.web.fetch_url_text", new=AsyncMock(return_value="The magic widget constant is 42.")):
        res = await we.run_generation_workflow(
            goal="Build something with the widgetlib skill",
            workspace_path=str(tmp_path),
            skill_gap_callback=skill_gap_cb,
            web_lookup_callback=lambda found: True,  # batch accepted, but content is empty
        )

    assert res["quality_gates_passed"] is True
    assert skill_gap_calls == [["widgetlib"]]  # fell through to human ask despite an accepted batch

    se = SkillEngine(str(skills_dir), load_global=False)
    se.discover_and_load()
    skill = se.get_skill("widgetlib")
    assert "The magic widget constant is 42." in skill.rules

@pytest.mark.asyncio
async def test_workflow_web_lookup_disabled_by_default_never_calls_search(tmp_path):
    """web_lookup_enabled defaults to False - a project that hasn't opted in must see
    zero behavior change, and search_web must never even be called."""
    skills_dir = tmp_path / "skills"
    skill_folder = skills_dir / "widgetlib"
    skill_folder.mkdir(parents=True)
    (skill_folder / "skill.yaml").write_text("name: widgetlib\ndescription: Test\ntags: [widgetlib]\n")
    (skill_folder / "rules.txt").write_text("Existing rule.\n")

    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.paths.skills = str(skills_dir)
    cfg.autonomy.run_verification_enabled = False
    assert cfg.autonomy.web_lookup_enabled is False
    assert cfg.search.base_url == ""
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code", "Design: Write app.py",
        '[{"filepath": "app.py", "content": "print(1)\\n"}]', "Review: Approved"
    ])

    we = WorkflowEngine(kernel, llm)
    with patch("kriya.tools.search.search_web", new=AsyncMock(side_effect=AssertionError("must not be called"))) as mock_search:
        res = await we.run_generation_workflow(
            goal="Build something with the widgetlib skill",
            workspace_path=str(tmp_path),
        )

    assert res["quality_gates_passed"] is True
    mock_search.assert_not_called()

@pytest.mark.asyncio
async def test_workflow_web_lookup_design_derived_bootstraps_new_skill(tmp_path):
    """The goal alone may not name any specific technology, but the Architect's design
    usually will once it makes real decisions - live lookup should catch that too, not
    just what the goal-text-only skill-gap check already covers."""
    from kriya.skills.skill import SkillEngine

    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()

    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.paths.skills = str(skills_dir)
    cfg.autonomy.run_verification_enabled = False
    cfg.autonomy.web_lookup_enabled = True
    cfg.autonomy.web_lookup_auto_approve = True  # bypass the pre-send confirmation gate for this test
    cfg.search.base_url = "http://fake-search:8080"
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    extraction_response = json.dumps({
        "rules": ["Use gizmolib.connect() to open a connection."],
        "examples": {},
        "conflicts": []
    })
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",                                     # Planner
        "Design: use gizmolib==3.1.0 to connect to the service",  # Architect names a new lib
        extraction_response,                                      # Stage 2B SkillGapAgent.extract_skill_update
        '[{"filepath": "app.py", "content": "print(1)\\n"}]',     # Developer
        "Review: Approved"                                        # Reviewer
    ])

    found_result = [{"term": "gizmolib", "title": "Gizmolib Docs", "url": "https://example.com/gizmolib", "snippet": "..."}]

    we = WorkflowEngine(kernel, llm)
    with patch("kriya.tools.search.search_web", new=AsyncMock(return_value=found_result)) as mock_search, \
         patch("kriya.tools.web.fetch_url_text", new=AsyncMock(return_value="gizmolib docs text")) as mock_fetch:
        res = await we.run_generation_workflow(
            goal="Build an app that talks to an external service",
            workspace_path=str(tmp_path),
            web_lookup_callback=lambda found: True,
        )

    assert res["quality_gates_passed"] is True
    mock_search.assert_called_once_with("gizmolib example", "http://fake-search:8080", top_k=3)
    mock_fetch.assert_called_once_with("https://example.com/gizmolib", quiet_on_failure=True)

    se = SkillEngine(str(skills_dir), load_global=False)
    se.discover_and_load()
    skill = se.get_skill("gizmolib")
    assert "Use gizmolib.connect() to open a connection." in skill.rules

@pytest.mark.asyncio
async def test_workflow_web_lookup_design_derived_falls_back_to_human_ask_on_empty_extraction(tmp_path):
    """Bug fix, design-derived path: if live lookup finds nothing usable for a
    technology only the Architect's design named, Kriya must fall back to asking a
    human (skill_gap_callback) rather than silently generating code against a
    technology it has zero grounding for."""
    from kriya.skills.skill import SkillEngine

    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()

    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.paths.skills = str(skills_dir)
    cfg.autonomy.run_verification_enabled = False
    cfg.autonomy.web_lookup_enabled = True
    cfg.autonomy.web_lookup_auto_approve = True  # bypass the pre-send confirmation gate for this test
    cfg.search.base_url = "http://fake-search:8080"
    cfg.search.top_k = 1
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    empty_extraction = json.dumps({"rules": [], "examples": {}, "conflicts": []})
    real_extraction = json.dumps({"rules": ["Use gizmolib.connect() to open a connection."], "examples": {}, "conflicts": []})
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",                                     # Planner
        "Design: use gizmolib==3.1.0 to connect to the service",  # Architect names a new lib
        empty_extraction,                                         # live lookup's candidate - nothing usable
        real_extraction,                                          # human-supplied reference, after fallback
        '[{"filepath": "app.py", "content": "print(1)\\n"}]',     # Developer
        "Review: Approved"                                        # Reviewer
    ])

    found_result = [{"title": "Gizmolib Landing Page", "url": "https://example.com/gizmolib", "snippet": "..."}]
    skill_gap_calls = []
    def skill_gap_cb(reason, names):
        skill_gap_calls.append(names)
        return "Use gizmolib.connect() to open a connection."

    we = WorkflowEngine(kernel, llm)
    with patch("kriya.tools.search.search_web", new=AsyncMock(return_value=found_result)), \
         patch("kriya.tools.web.fetch_url_text", new=AsyncMock(return_value="Marketing copy, no specifics.")):
        res = await we.run_generation_workflow(
            goal="Build an app that talks to an external service",
            workspace_path=str(tmp_path),
            skill_gap_callback=skill_gap_cb,
            web_lookup_callback=lambda found: True,
        )

    assert res["quality_gates_passed"] is True
    assert skill_gap_calls == [["gizmolib"]]  # fell back to asking a human for the design-derived gap

    se = SkillEngine(str(skills_dir), load_global=False)
    se.discover_and_load()
    skill = se.get_skill("gizmolib")
    assert "Use gizmolib.connect() to open a connection." in skill.rules


@pytest.mark.asyncio
async def test_workflow_normalizes_absolute_filepath_from_developer_agent(tmp_path):
    """Reproduces a real observed failure: the Developer Agent sometimes returns an
    absolute path instead of a relative one. os.path.join(worktree_path, filepath)
    silently discards worktree_path when filepath is absolute, so the file would land
    directly in the real workspace (bypassing the sandbox) and later crash with
    shutil.SameFileError when the apply step tries to copy worktree -> workspace with
    source == destination. Needs a real git repo so a real worktree gets created
    (worktree_path != workspace_path) - the bug doesn't manifest in the fallback path
    where they're already the same directory.
    """
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("placeholder\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=tmp_path, check=True)

    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    absolute_filepath = str(tmp_path / "app.py")
    file_list_response = json.dumps([{"filepath": absolute_filepath, "content": "print(1)\n"}])

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        # Deliberately no literal "app.py" mention here (unlike other tests) - this
        # test is specifically about the model's own file-list response containing
        # an absolute path, which only happens on the ask-the-model path. A design
        # mentioning "app.py" would activate known_target_files on attempt 1 and
        # fix the filepath deterministically before the model ever gets a say.
        "Design: a single Python script with one entrypoint",
        file_list_response,
        "Review: Approved"
    ])

    we = WorkflowEngine(kernel, llm)
    res = await we.run_generation_workflow(
        goal="Create a small script",
        workspace_path=str(tmp_path)
    )

    assert res["quality_gates_passed"] is True
    assert res["files"] == ["app.py"]
    assert (tmp_path / "app.py").exists()
    with open(tmp_path / "app.py") as f:
        assert f.read() == "print(1)\n"


@pytest.mark.asyncio
async def test_workflow_successful_run_deletes_its_own_checkpoint(tmp_path):
    """A checkpoint is only useful across a crash - a normal completion should
    leave nothing behind for a future --resume to (mis)pick up."""
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write math.py",
        '[{"filepath": "math.py", "content": "def add(a,b):\\n    return a+b"}]',
        "Review: Approved"
    ])

    we = WorkflowEngine(kernel, llm)
    res = await we.run_generation_workflow(goal="Create math library", workspace_path=str(tmp_path))

    assert res["quality_gates_passed"] is True
    assert res.get("run_id")
    assert not os.path.exists(checkpoint_path(str(tmp_path), res["run_id"]))
    assert find_latest_checkpoint(str(tmp_path)) is None


@pytest.mark.asyncio
async def test_workflow_resumes_from_plan_checkpoint_skips_planner_only(tmp_path):
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    goal = "Create math library"

    _seed_checkpoint(tmp_path, cfg, goal, "ckpt-plan", "plan", plan="Step 1: Write code (from checkpoint)")

    llm.complete = AsyncMock(side_effect=[
        "Design: Write math.py",  # Architect
        '[{"filepath": "math.py", "content": "def add(a,b):\\n    return a+b"}]',  # Developer
        "Review: Approved",  # Reviewer
    ])

    we = WorkflowEngine(kernel, llm)
    res = await we.run_generation_workflow(goal=goal, workspace_path=str(tmp_path), resume=True)

    assert res["quality_gates_passed"] is True
    assert res["plan"] == "Step 1: Write code (from checkpoint)"
    assert llm.complete.await_count == 3  # Planner call skipped


@pytest.mark.asyncio
async def test_workflow_resumes_from_design_checkpoint_skips_planner_and_architect(tmp_path):
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    goal = "Create math library"

    _seed_checkpoint(
        tmp_path, cfg, goal, "ckpt-design", "design",
        plan="Step 1 (from checkpoint)", design="Design: Write math.py (from checkpoint)",
    )

    llm.complete = AsyncMock(side_effect=[
        '[{"filepath": "math.py", "content": "def add(a,b):\\n    return a+b"}]',  # Developer
        "Review: Approved",  # Reviewer
    ])

    we = WorkflowEngine(kernel, llm)
    res = await we.run_generation_workflow(goal=goal, workspace_path=str(tmp_path), resume_id="ckpt-design")

    assert res["quality_gates_passed"] is True
    assert res["design"] == "Design: Write math.py (from checkpoint)"
    assert llm.complete.await_count == 2  # Planner + Architect calls both skipped


@pytest.mark.asyncio
async def test_workflow_resumes_from_developer_success_checkpoint_skips_quality_gates(tmp_path):
    """The most valuable resume point: Developer generation + compile/test gates
    already passed before the crash, so only the human-approval/apply/regression
    tail and the Reviewer need to run - no re-generation, no re-compiling."""
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    goal = "Create math library"

    _seed_checkpoint(
        tmp_path, cfg, goal, "ckpt-dev", "developer_success",
        plan="Step 1 (from checkpoint)",
        design="Design: Write math.py (from checkpoint)",
        final_files={"math.py": "def add(a,b):\n    return a+b"},
        original_files={},
        gate_outcomes=[],
        model_hops=[],
        retry_count=0,
    )

    llm.complete = AsyncMock(side_effect=["Review: Approved"])  # only the Reviewer should run

    we = WorkflowEngine(kernel, llm)
    res = await we.run_generation_workflow(goal=goal, workspace_path=str(tmp_path), resume=True)

    assert res["quality_gates_passed"] is True
    assert "math.py" in res["files"]
    assert (tmp_path / "math.py").read_text() == "def add(a,b):\n    return a+b"
    assert llm.complete.await_count == 1  # Planner, Architect, Developer all skipped


@pytest.mark.asyncio
async def test_workflow_refuses_resume_on_goal_drift(tmp_path):
    """Strict drift detection: any goal-text difference since the checkpoint was
    saved must invalidate it entirely and fall back to a normal fresh run rather
    than a partial/best-effort resume."""
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    _seed_checkpoint(tmp_path, cfg, "Create math library", "ckpt-stale", "plan", plan="Stale plan")

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write math.py",
        '[{"filepath": "math.py", "content": "def add(a,b):\\n    return a+b"}]',
        "Review: Approved",
    ])

    we = WorkflowEngine(kernel, llm)
    res = await we.run_generation_workflow(
        goal="Create a DIFFERENT math library",  # deliberately not the checkpoint's goal
        workspace_path=str(tmp_path),
        resume=True,
    )

    assert res["quality_gates_passed"] is True
    assert res["plan"] == "Step 1: Write code"  # real Planner call, not the stale checkpoint value
    assert llm.complete.await_count == 4  # nothing was skipped


@pytest.mark.asyncio
async def test_workflow_resume_with_no_checkpoint_falls_back_to_fresh_run(tmp_path):
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write math.py",
        '[{"filepath": "math.py", "content": "def add(a,b):\\n    return a+b"}]',
        "Review: Approved",
    ])

    we = WorkflowEngine(kernel, llm)
    res = await we.run_generation_workflow(goal="Create math library", workspace_path=str(tmp_path), resume=True)

    assert res["quality_gates_passed"] is True
    assert llm.complete.await_count == 4


def test_checkpoint_save_load_delete_roundtrip(tmp_path):
    save_checkpoint(str(tmp_path), "run-1", {"stage": "plan", "plan": "hello"})
    loaded = load_checkpoint(str(tmp_path), "run-1")
    assert loaded["stage"] == "plan"
    assert loaded["plan"] == "hello"
    assert loaded["run_id"] == "run-1"

    from kriya.workflow.checkpoint import delete_checkpoint
    delete_checkpoint(str(tmp_path), "run-1")
    assert load_checkpoint(str(tmp_path), "run-1") is None


def test_checkpoint_workspace_fingerprint_changes_on_new_commit(tmp_path):
    _init_git_repo(tmp_path)
    fp1 = compute_workspace_fingerprint(str(tmp_path))
    assert fp1 is not None and fp1.endswith(":clean")

    (tmp_path / "app.py").write_text("print(1)\n")
    fp2 = compute_workspace_fingerprint(str(tmp_path))
    assert fp2.endswith(":dirty")
    assert fp2 != fp1

    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "second"], cwd=tmp_path, check=True)
    fp3 = compute_workspace_fingerprint(str(tmp_path))
    assert fp3.endswith(":clean")
    assert fp3 != fp1 and fp3 != fp2


def test_checkpoint_workspace_fingerprint_none_for_non_git_dir(tmp_path):
    assert compute_workspace_fingerprint(str(tmp_path)) is None


def test_checkpoint_config_fingerprint_stable_and_sensitive_to_changes():
    cfg_a = AppConfig()
    cfg_b = AppConfig()
    assert compute_config_fingerprint(cfg_a.model_dump()) == compute_config_fingerprint(cfg_b.model_dump())

    cfg_b.autonomy.run_verification_enabled = not cfg_b.autonomy.run_verification_enabled
    assert compute_config_fingerprint(cfg_a.model_dump()) != compute_config_fingerprint(cfg_b.model_dump())


def test_checkpoint_find_latest_returns_most_recently_saved(tmp_path):
    save_checkpoint(str(tmp_path), "older", {"stage": "plan"})
    import time as _time
    _time.sleep(0.01)
    save_checkpoint(str(tmp_path), "newer", {"stage": "design"})
    assert find_latest_checkpoint(str(tmp_path)) == "newer"


def test_extract_error_search_terms_finds_maven_coordinate():
    error = (
        "[ERROR] Failed to execute goal org.codehaus.mojo:exec-maven-plugin:3.1.0:java "
        "for parameter arguments: Cannot store value into array"
    )
    assert extract_error_search_terms(error) == ["org.codehaus.mojo:exec-maven-plugin"]

def test_extract_error_search_terms_dedups_multiple_occurrences():
    error = (
        "org.codehaus.mojo:exec-maven-plugin failed. See org.codehaus.mojo:exec-maven-plugin docs."
    )
    assert extract_error_search_terms(error) == ["org.codehaus.mojo:exec-maven-plugin"]

def test_extract_error_search_terms_finds_multiple_distinct_coordinates():
    error = "org.apache.maven.plugins:maven-compiler-plugin failed after org.codehaus.mojo:exec-maven-plugin succeeded"
    assert extract_error_search_terms(error) == [
        "org.apache.maven.plugins:maven-compiler-plugin",
        "org.codehaus.mojo:exec-maven-plugin",
    ]

def test_extract_error_search_terms_ignores_plain_symbols_and_paths():
    # Neither a bare class/package name (no colon) nor a filesystem path (colon-free
    # on this platform, and even Windows-style drive letters don't match groupId
    # shape) should be treated as a safe search term - only real dotted-namespace
    # coordinate syntax should match.
    error = (
        "cannot find symbol: class JmsConnectionFactory\n"
        "location: class com.example.CacheAndMessagingClient\n"
        "at /Users/dev/project/src/main/java/com/example/App.java:19"
    )
    assert extract_error_search_terms(error) == []

def test_extract_error_search_terms_ignores_junit_stack_trace_even_though_source_locations_now_recognizes_it():
    """Explicit egress-safety regression test: extract_error_source_locations()
    was just extended (2026-08-07) to recognize a JUnit/JVM stack trace's
    file:line locator shape, purely to show real source lines to the LOCAL
    model. This confirms that extension has zero effect on the separate,
    narrowly-scoped live-lookup term extractor - a full real stack trace
    (class/method/file names, the exact kind of project-specific content
    that must never reach an external search query) still yields no terms
    at all, the same as before the source-locations fix."""
    error = (
        "java.nio.BufferUnderflowException\n"
        "\tat java.base/java.nio.HeapByteBuffer.get(HeapByteBuffer.java:194)\n"
        "\tat com.example.protocol.ProtocolParser.decode(ProtocolParser.java:60)\n"
        "\tat com.example.ProtocolTest.testNormalRoundTrip(ProtocolTest.java:26)\n"
    )
    assert extract_error_search_terms(error) == []

def test_extract_error_search_terms_excludes_projects_own_coordinate():
    # Regression test for a real bug found live during golden-use-case
    # validation: Maven's own build banner prints the PROJECT'S OWN
    # groupId:artifactId on every single build, success or failure
    # ("[INFO] ----------------< com.example:ignite-qpid-integration
    # >-----------------") - the exact same coordinate shape this function
    # looks for. Without exclusion, a repeated-failure live-lookup recovery
    # attempt wastes its one shot searching for the project's own made-up
    # artifact ID instead of anything related to the actual failure.
    error = (
        "[INFO] ----------------< com.example:ignite-qpid-integration >-----------------\n"
        "[INFO] Building ignite-qpid-integration 1.0-SNAPSHOT\n"
        "[ERROR] Failed to execute goal org.codehaus.mojo:exec-maven-plugin:3.1.0:java "
        "for parameter arguments: Cannot store value into array"
    )
    result = extract_error_search_terms(error, exclude_coordinates=["com.example:ignite-qpid-integration"])
    assert result == ["org.codehaus.mojo:exec-maven-plugin"]
    assert "com.example:ignite-qpid-integration" not in result

def test_extract_error_search_terms_no_exclusion_when_none_given():
    error = "[INFO] ----------------< com.example:ignite-qpid-integration >-----------------"
    assert extract_error_search_terms(error) == ["com.example:ignite-qpid-integration"]

def test_extract_error_search_terms_finds_unresolved_import_matching_dependency():
    # Regression test for a real bug found live during M3 golden-use-case
    # validation: a wrong-import-path compile failure (e.g. writing
    # `import org.apache.ignite.cache.IgniteCache;` when the class actually
    # lives at the top-level org.apache.ignite package) has NO groupId:
    # artifactId coordinate anywhere in it - just "symbol: class X" /
    # "location: package Y" - so it previously yielded zero search terms no
    # matter how many times it recurred identically. This is safety-bounded:
    # only becomes a term because "org.apache.ignite.cache" shares a dot-
    # prefix with a coordinate the caller says is a REAL project dependency.
    error = (
        "[ERROR] .../IntegrationApp.java:[5,31] cannot find symbol\n"
        "[ERROR]   symbol:   class IgniteCache\n"
        "[ERROR]   location: package org.apache.ignite.cache\n"
    )
    result = extract_error_search_terms(
        error, dependency_coordinates=["org.apache.ignite:ignite-core"]
    )
    assert result == ["org.apache.ignite:ignite-core IgniteCache"]

def test_extract_error_search_terms_ignores_unresolved_import_with_no_matching_dependency():
    # Same wrong-import shape, but no supplied dependency's groupId matches
    # the erroneous package - must NOT become a search term, since that
    # would mean sending an arbitrary/unverified symbol name outbound.
    error = (
        "[ERROR] .../IntegrationApp.java:[5,31] cannot find symbol\n"
        "[ERROR]   symbol:   class IgniteCache\n"
        "[ERROR]   location: package org.apache.ignite.cache\n"
    )
    result = extract_error_search_terms(
        error, dependency_coordinates=["org.apache.qpid:qpid-broker-core"]
    )
    assert result == []

def test_extract_error_search_terms_ignores_location_class_shape_even_with_dependencies():
    # "location: class X" (javac's shape for a symbol never imported at all,
    # not a wrong-import-path mistake) must never match, even when
    # dependency_coordinates are supplied - only "location: package Y" is
    # the targeted, safety-bounded shape.
    error = (
        "cannot find symbol: class JmsConnectionFactory\n"
        "location: class com.example.CacheAndMessagingClient\n"
    )
    result = extract_error_search_terms(
        error, dependency_coordinates=["org.apache.qpid:qpid-jms-client"]
    )
    assert result == []

def test_extract_error_source_locations_finds_absolute_worktree_path():
    error = (
        "[ERROR] /Users/dev/proj/.kriya/worktree/src/main/java/com/example/"
        "IntegrationApp.java:[91,79] incompatible types: java.lang.Object cannot be converted to com.example.Person"
    )
    assert extract_error_source_locations(error) == [("IntegrationApp.java", 91)]

def test_extract_error_source_locations_dedups_and_finds_multiple():
    error = (
        "[ERROR] .../IntegrationApp.java:[91,79] incompatible types\n"
        "[ERROR] .../IntegrationApp.java:[91,79] incompatible types\n"
        "[ERROR] .../Other.java:[5,1] cannot find symbol"
    )
    assert extract_error_source_locations(error) == [("IntegrationApp.java", 91), ("Other.java", 5)]

def test_extract_error_source_locations_no_match_returns_empty():
    assert extract_error_source_locations("Process exited with code 1.") == []

def test_extract_error_source_locations_finds_junit_stack_trace_shape():
    """Regression test for a real bug found live (2026-08-07,
    kriya-protocol-parser-app): a JUnit/JVM stack trace's own file:line
    locator ('at pkg.Class.method(File.java:60)') carries the identical
    precision a compile error's 'File.java:[line,col]' shape does, but the
    compile-error-only regex never recognized it - so a test failure with a
    real stack trace got zero source-line grounding and no anchored-edit
    preference, unlike a compile error. The model's own fix-analysis
    correctly diagnosed a BufferUnderflowException's root cause but, with
    no precise anchor, fell back to full-file regeneration and
    reintroduced an equivalent bug."""
    error = (
        "java.nio.BufferUnderflowException\n"
        "\tat java.base/java.nio.HeapByteBuffer.get(HeapByteBuffer.java:194)\n"
        "\tat com.example.protocol.ProtocolParser.decode(ProtocolParser.java:60)\n"
        "\tat com.example.ProtocolTest.testNormalRoundTrip(ProtocolTest.java:26)\n"
    )
    assert extract_error_source_locations(error) == [
        ("HeapByteBuffer.java", 194),
        ("ProtocolParser.java", 60),
        ("ProtocolTest.java", 26),
    ]

def test_extract_error_source_locations_handles_both_shapes_together():
    # A single retry attempt can plausibly carry both shapes if a compile
    # warning and a runtime stack trace both appear in the same captured
    # output - both must resolve correctly, not just whichever comes first.
    error = (
        "[ERROR] .../App.java:[10,5] incompatible types\n"
        "\tat com.example.Worker.run(Worker.java:42)\n"
    )
    assert extract_error_source_locations(error) == [("App.java", 10), ("Worker.java", 42)]

def test_extract_error_source_locations_finds_python_syntax_error_shape():
    """Regression test for a real bug found live (2026-08-13, python_greeter,
    reproduced identically across two separate eval-harness runs): the SAME
    "no anchor -> full-file regeneration -> stated fix gets lost in the
    rewrite" failure mode as the JUnit/stack-trace gap above, this time for
    Python. PolymorphicValidator.run_compile_check()'s own SyntaxError
    formatting ('Syntax error in {file} line {N}: {text} ({msg})',
    kriya/tools/validate.py) was never recognized by the compile-error-only
    regex, so every Python goal's syntax-error retries got zero source-line
    grounding and no anchored-edit preference - unlike a Java compile error
    carrying the identical file:line precision. Live consequence: a model
    (glm-4.7-flash) correctly diagnosed a one-line fix in its own FIX
    ANALYSIS text on 4 consecutive retries and never once implemented it,
    forced to blindly rewrite the whole file from scratch each time with no
    anchor to patch against."""
    error = "Syntax error in greet.py line 6: [VERIFICATION] PASS (invalid syntax)"
    assert extract_error_source_locations(error) == [("greet.py", 6)]

def test_extract_error_source_locations_handles_all_three_shapes_together():
    error = (
        "[ERROR] .../App.java:[10,5] incompatible types\n"
        "\tat com.example.Worker.run(Worker.java:42)\n"
        "Syntax error in greet.py line 6: [VERIFICATION] PASS (invalid syntax)\n"
    )
    assert extract_error_source_locations(error) == [
        ("App.java", 10), ("Worker.java", 42), ("greet.py", 6),
    ]

def test_build_error_source_context_extracts_real_source_window(tmp_path):
    (tmp_path / "IntegrationApp.java").write_text(
        "\n".join(f"line {i}" for i in range(1, 20))
    )
    error = "[ERROR] .../IntegrationApp.java:[10,5] incompatible types"
    result = _build_error_source_context(str(tmp_path), error, known_files=["IntegrationApp.java"])
    assert "IntegrationApp.java" in result
    snippet = result["IntegrationApp.java"]
    assert ">> 10: line 10" in snippet
    assert "9: line 9" in snippet
    assert "11: line 11" in snippet
    assert "line 1\n" not in snippet  # far-away lines excluded from the window

def test_build_error_source_context_skips_unknown_file(tmp_path):
    error = "[ERROR] .../NotWritten.java:[10,5] incompatible types"
    result = _build_error_source_context(str(tmp_path), error, known_files=["IntegrationApp.java"])
    assert result == {}

def test_build_error_source_context_no_locations_returns_empty(tmp_path):
    result = _build_error_source_context(str(tmp_path), "Process exited with code 1.", known_files=["App.java"])
    assert result == {}

def test_build_error_source_context_extracts_python_syntax_error_window(tmp_path):
    """End-to-end sibling to test_build_error_source_context_extracts_real_source_window
    for the Python SyntaxError shape - proves the fix actually reaches the
    same source-window-extraction path a Java compile error already uses,
    not just that extract_error_source_locations() parses the location."""
    (tmp_path / "greet.py").write_text(
        "def greet(name: str) -> str:\n    return f\"Hello, {name}!\"\n\n"
        "[VERIFICATION] PASS\nprint(greet('World'))\n"
    )
    error = "Syntax error in greet.py line 4: [VERIFICATION] PASS (invalid syntax)"
    result = _build_error_source_context(str(tmp_path), error, known_files=["greet.py"])
    assert "greet.py" in result
    assert ">> 4: [VERIFICATION] PASS" in result["greet.py"]

def test_build_error_source_context_covers_every_file_sharing_a_basename(tmp_path):
    """Regression test for the Row 5 basename-collision bug found by code
    inspection during the 2026-08-14 file-attribution-consolidation review:
    _build_error_source_context() used to build `{os.path.basename(f): f for
    f in known_files}` - a plain dict, silently keeping only the LAST known
    file for a shared basename (e.g. the same-named class in two packages).
    Two known files here share the basename 'Worker.java'; the fix
    (_files_by_basename() in failure_grounding.py) must surface context for
    BOTH, not just whichever happened to be iterated last."""
    (tmp_path / "pkg_a").mkdir()
    (tmp_path / "pkg_b").mkdir()
    (tmp_path / "pkg_a" / "Worker.java").write_text("\n".join(f"a-line {i}" for i in range(1, 15)))
    (tmp_path / "pkg_b" / "Worker.java").write_text("\n".join(f"b-line {i}" for i in range(1, 15)))
    error = "[ERROR] .../Worker.java:[5,5] incompatible types"
    result = _build_error_source_context(
        str(tmp_path), error, known_files=["pkg_a/Worker.java", "pkg_b/Worker.java"]
    )
    assert set(result.keys()) == {"pkg_a/Worker.java", "pkg_b/Worker.java"}
    assert ">> 5: a-line 5" in result["pkg_a/Worker.java"]
    assert ">> 5: b-line 5" in result["pkg_b/Worker.java"]

def test_resolve_file_locations_covers_every_file_sharing_a_basename():
    """Sibling regression test to
    test_build_error_source_context_covers_every_file_sharing_a_basename,
    for _resolve_file_locations() - the other of the two call sites that
    independently had the same Row 5 basename-collision bug (a Failure's own
    file_locations, not a prompt-display snippet). Previously had ZERO test
    coverage of any kind, found by code inspection alongside the bug itself."""
    error = "[ERROR] .../Worker.java:[5,5] incompatible types"
    locations = _resolve_file_locations(error, known_files=["pkg_a/Worker.java", "pkg_b/Worker.java"])
    assert {loc.filepath for loc in locations} == {"pkg_a/Worker.java", "pkg_b/Worker.java"}
    assert all(loc.line == 5 for loc in locations)

def test_resolve_file_locations_single_file_unaffected():
    error = "[ERROR] .../App.java:[10,5] incompatible types"
    locations = _resolve_file_locations(error, known_files=["App.java"])
    assert len(locations) == 1
    assert locations[0].filepath == "App.java"
    assert locations[0].line == 10

@pytest.mark.asyncio
async def test_get_or_start_jdtls_client_returns_none_when_jdtls_not_found():
    with patch("kriya.tools.lsp.find_jdtls", return_value=None):
        result = await _get_or_start_jdtls_client(None, "/fake/project")
    assert result is None

@pytest.mark.asyncio
async def test_get_or_start_jdtls_client_reuses_existing_client():
    existing = object()
    with patch("kriya.tools.lsp.find_jdtls") as mock_find:
        result = await _get_or_start_jdtls_client(existing, "/fake/project")
    assert result is existing
    mock_find.assert_not_called()

@pytest.mark.asyncio
async def test_get_or_start_jdtls_client_starts_new_client_when_found():
    mock_client = AsyncMock()
    with patch("kriya.tools.lsp.find_jdtls", return_value="/usr/local/bin/jdtls"), \
         patch("kriya.tools.lsp.JdtlsClient", return_value=mock_client):
        result = await _get_or_start_jdtls_client(None, "/fake/project")
    assert result is mock_client
    mock_client.start.assert_awaited_once()

@pytest.mark.asyncio
async def test_get_or_start_jdtls_client_degrades_cleanly_if_start_fails():
    mock_client = AsyncMock()
    mock_client.start.side_effect = RuntimeError("jdtls crashed on launch")
    with patch("kriya.tools.lsp.find_jdtls", return_value="/usr/local/bin/jdtls"), \
         patch("kriya.tools.lsp.JdtlsClient", return_value=mock_client):
        result = await _get_or_start_jdtls_client(None, "/fake/project")
    assert result is None

@pytest.mark.asyncio
async def test_get_or_start_jdtls_client_logs_start_failure_at_warning_not_debug(caplog):
    """Regression test for a real gap found live (2026-08-03): a real
    'cannot find symbol' compile error went completely uncaught by LSP
    grounding during an actual generation run, and the only line that could
    have explained why was logged at DEBUG - invisible at the default log
    level, making the whole feature silently undebuggable from the field.
    'jdtls found but failed to start' must be visible at WARNING."""
    mock_client = AsyncMock()
    mock_client.start.side_effect = RuntimeError("jdtls crashed on launch")
    with caplog.at_level(logging.WARNING, logger="kriya.workflow.workflow"), \
         patch("kriya.tools.lsp.find_jdtls", return_value="/usr/local/bin/jdtls"), \
         patch("kriya.tools.lsp.JdtlsClient", return_value=mock_client):
        await _get_or_start_jdtls_client(None, "/fake/project")
    assert any("jdtls" in r.message.lower() and "failed to start" in r.message.lower() for r in caplog.records)

@pytest.mark.asyncio
async def test_get_or_start_jdtls_client_logs_not_found_at_info(caplog):
    """Regression test for a residual visibility gap found live, 2026-08-03,
    right after the WARNING-level fix above: a successful jdtls check that
    finds zero diagnostics produces zero log output by design (not a
    failure), which meant 'LSP grounding never engaged this run' and 'it
    engaged and had nothing to add' were still indistinguishable from the
    log even after the failure path was fixed. This one-time INFO note on
    the not-found path (previously completely silent) closes that gap."""
    with caplog.at_level(logging.INFO, logger="kriya.workflow.lsp_integration"), \
         patch("kriya.tools.lsp.find_jdtls", return_value=None):
        result = await _get_or_start_jdtls_client(None, "/fake/project")
    assert result is None
    assert any("jdtls" in r.message.lower() and "not found" in r.message.lower() for r in caplog.records)

@pytest.mark.asyncio
async def test_get_or_start_jdtls_client_logs_success_at_info(caplog):
    """The mirror image of the not-found case above - a successful start
    must also leave a positive trace, so a run's log can affirmatively
    confirm LSP grounding was active rather than leaving it ambiguous
    whether a lack of ground-truth text means 'didn't run' or 'ran and
    found nothing wrong'."""
    mock_client = AsyncMock()
    with caplog.at_level(logging.INFO, logger="kriya.workflow.lsp_integration"), \
         patch("kriya.tools.lsp.find_jdtls", return_value="/usr/local/bin/jdtls"), \
         patch("kriya.tools.lsp.JdtlsClient", return_value=mock_client):
        result = await _get_or_start_jdtls_client(None, "/fake/project")
    assert result is mock_client
    assert any("jdtls" in r.message.lower() and "active" in r.message.lower() for r in caplog.records)

@pytest.mark.asyncio
async def test_build_lsp_diagnostics_context_only_checks_java_files(tmp_path):
    (tmp_path / "App.java").write_text("class App {}")
    (tmp_path / "pom.xml").write_text("<project></project>")
    mock_client = AsyncMock()
    mock_client.check_file.return_value = [
        {"severity": 1, "range": {"start": {"line": 0}}, "message": "cannot resolve import"}
    ]
    result = await _build_lsp_diagnostics_context(mock_client, str(tmp_path), ["App.java", "pom.xml"])
    assert "App.java" in result
    assert "pom.xml" not in result
    assert mock_client.check_file.call_count == 1

@pytest.mark.asyncio
async def test_build_lsp_diagnostics_context_skips_files_with_no_errors(tmp_path):
    (tmp_path / "App.java").write_text("class App {}")
    mock_client = AsyncMock()
    mock_client.check_file.return_value = []
    result = await _build_lsp_diagnostics_context(mock_client, str(tmp_path), ["App.java"])
    assert result == {}

@pytest.mark.asyncio
async def test_build_lsp_diagnostics_context_survives_a_check_failure(tmp_path):
    (tmp_path / "App.java").write_text("class App {}")
    (tmp_path / "Other.java").write_text("class Other {}")
    mock_client = AsyncMock()
    mock_client.check_file.side_effect = [
        RuntimeError("timeout"),
        [{"severity": 1, "range": {"start": {"line": 0}}, "message": "cannot resolve import"}],
    ]
    result = await _build_lsp_diagnostics_context(mock_client, str(tmp_path), ["App.java", "Other.java"])
    assert "App.java" not in result
    assert "Other.java" in result

@pytest.mark.asyncio
async def test_workflow_merges_lsp_diagnostics_into_retry_prompt_for_java_project(tmp_path):
    """End-to-end wiring test: when jdtls is available (mocked - no real
    jdtls needed) and the project is a Maven Java project, a real compile
    failure's retry prompt for the implicated file includes the LSP
    diagnostic's forceful ground-truth framing, merged into the same
    error_source_context scoping mechanism already used for the generic
    file:line:col source-context fix."""
    _init_git_repo(tmp_path)
    (tmp_path / "pom.xml").write_text("<project></project>")
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write App.java",
        "Review: Approved",
    ])

    we = WorkflowEngine(kernel, llm)
    mock_jdtls = AsyncMock()
    mock_jdtls.check_file.return_value = [
        {"severity": 1, "range": {"start": {"line": 4}}, "message": "The import org.apache.ignite.cache.IgniteCache cannot be resolved"}
    ]
    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check") as mock_compile, \
         patch("kriya.tools.validate.PolymorphicValidator.run_tests", return_value={"success": True, "output": ""}), \
         patch("kriya.tools.lsp.find_jdtls", return_value="/usr/local/bin/jdtls"), \
         patch("kriya.tools.lsp.JdtlsClient", return_value=mock_jdtls):
        mock_compile.side_effect = [
            {"success": False, "output": "[ERROR] .../App.java:[5,1] cannot find symbol"},
            {"success": True, "output": ""},
        ]
        we.developer.run_generation = AsyncMock(
            side_effect=[
                [{"filepath": "App.java", "content": "class App {}"}],
                [{"filepath": "App.java", "content": "class App {}"}],
            ]
        )
        res = await we.run_generation_workflow(goal="Create a Java app", workspace_path=str(tmp_path))

    assert res["quality_gates_passed"] is True
    mock_jdtls.start.assert_awaited_once()
    mock_jdtls.shutdown.assert_awaited_once()
    second_call_kwargs = we.developer.run_generation.call_args_list[1].kwargs
    error_source_context = second_call_kwargs["error_source_context"]
    assert "App.java" in error_source_context
    assert "ground truth" in error_source_context["App.java"].lower()
    assert "IgniteCache cannot be resolved" in error_source_context["App.java"]

@pytest.mark.asyncio
async def test_workflow_surfaces_lsp_warning_when_jdtls_found_but_fails_to_start(tmp_path):
    """jdtls being found on PATH but failing to start is a real, actionable
    problem (unlike simply not being installed) - must be surfaced in the
    final result dict and printed by generate/fix regardless of pass/fail,
    same treatment as toolchain_warning."""
    _init_git_repo(tmp_path)
    (tmp_path / "pom.xml").write_text("<project></project>")
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write App.java",
        "Review: Approved",
    ])

    we = WorkflowEngine(kernel, llm)
    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check") as mock_compile, \
         patch("kriya.tools.validate.PolymorphicValidator.run_tests", return_value={"success": True, "output": ""}), \
         patch("kriya.tools.lsp.find_jdtls", return_value="/usr/local/bin/jdtls"), \
         patch("kriya.tools.lsp.JdtlsClient") as mock_jdtls_cls:
        mock_jdtls_cls.return_value.start = AsyncMock(side_effect=RuntimeError("jdtls crashed on launch"))
        mock_compile.side_effect = [
            {"success": False, "output": "[ERROR] .../App.java:[5,1] cannot find symbol"},
            {"success": True, "output": ""},
        ]
        we.developer.run_generation = AsyncMock(
            side_effect=[
                [{"filepath": "App.java", "content": "class App {}"}],
                [{"filepath": "App.java", "content": "class App {}"}],
            ]
        )
        res = await we.run_generation_workflow(goal="Create a Java app", workspace_path=str(tmp_path))

    assert res["quality_gates_passed"] is True
    assert res["lsp_warning"] is not None
    assert "jdtls" in res["lsp_warning"].lower() and "failed to start" in res["lsp_warning"].lower()

@pytest.mark.asyncio
async def test_workflow_skips_lsp_gracefully_when_jdtls_not_found(tmp_path):
    """A project with no jdtls installed must proceed exactly as before this
    feature existed - zero errors, zero behavior change, confirmed by
    reusing the exact same retry scenario without mocking JdtlsClient at all."""
    _init_git_repo(tmp_path)
    (tmp_path / "pom.xml").write_text("<project></project>")
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write App.java",
        "Review: Approved",
    ])

    we = WorkflowEngine(kernel, llm)
    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check") as mock_compile, \
         patch("kriya.tools.validate.PolymorphicValidator.run_tests", return_value={"success": True, "output": ""}), \
         patch("kriya.tools.lsp.find_jdtls", return_value=None):
        mock_compile.side_effect = [
            {"success": False, "output": "[ERROR] .../App.java:[5,1] cannot find symbol"},
            {"success": True, "output": ""},
        ]
        we.developer.run_generation = AsyncMock(
            return_value=[{"filepath": "App.java", "content": "class App {}"}]
        )
        res = await we.run_generation_workflow(goal="Create a Java app", workspace_path=str(tmp_path))

    assert res["quality_gates_passed"] is True

def test_normalize_error_for_repeat_detection_strips_maven_timing_lines():
    # Real, byte-for-byte identical underlying failure (Qpid's SystemLauncher
    # UnsupportedOperationException) as captured across two separate Kriya
    # retry attempts - only the build-duration and timestamp lines differ.
    error_a = (
        "Exception in thread \"main\" java.lang.UnsupportedOperationException: "
        "getSubject is not supported\n"
        "\tat javax.security.auth.Subject.getSubject(Subject.java:347)\n"
        "[INFO] ------------------------------------------------------------------------\n"
        "[INFO] BUILD FAILURE\n"
        "[INFO] ------------------------------------------------------------------------\n"
        "[INFO] Total time:  0.832 s\n"
        "[INFO] Finished at: 2026-08-02T20:23:47+05:30\n"
    )
    error_b = error_a.replace("0.832 s", "1.104 s").replace(
        "2026-08-02T20:23:47+05:30", "2026-08-02T20:31:12+05:30"
    )
    assert error_a != error_b
    assert _normalize_error_for_repeat_detection(error_a) == _normalize_error_for_repeat_detection(error_b)

def test_normalize_error_for_repeat_detection_preserves_differing_errors():
    error_a = "Exception in thread \"main\" java.lang.UnsupportedOperationException: getSubject is not supported"
    error_b = "Exception in thread \"main\" java.lang.NullPointerException: Cannot invoke foo()"
    assert _normalize_error_for_repeat_detection(error_a) != _normalize_error_for_repeat_detection(error_b)

def test_classify_environment_failure_detects_jvm_startup_error():
    # Real, byte-for-byte text captured during golden-use-case validation: a JVM
    # startup flag correct for JDK 17.0.10 became fatal under JDK 26 (JEP 486
    # removed the Security Manager entirely).
    error = (
        "Error occurred during initialization of VM\n"
        "java.lang.Error: A command line option has attempted to allow or "
        "enable the Security Manager. Enabling a Security Manager is not "
        "supported."
    )
    result = classify_environment_failure(error)
    assert result is not None
    assert "JVM failed during its own startup" in result

def test_classify_environment_failure_detects_qpid_jdk24_security_manager_api_crash():
    """Regression test for a real bug found live, 2026-08-07
    (ignite_qpid_person): immediately after _strip_jdk_incompatible_jvm_flags
    started correctly removing the now-forbidden -Djava.security.manager=allow
    flag on a resolved JDK 26 target, the broker crashed on a DIFFERENT,
    previously-unseen error instead - Qpid Broker-J's own internal logging
    calls Subject.getSubject(), an API tied to the Security Manager JEP 486
    permanently removed in JDK 24+. Without this classification, the retry
    loop burned two full attempts trying to code-fix a genuine library/JDK
    incompatibility no code regeneration could ever resolve."""
    error = (
        "Exception in thread \"main\" java.lang.UnsupportedOperationException: getSubject is not supported\n"
        "\tat java.base/javax.security.auth.Subject.getSubject(Subject.java:277)\n"
        "\tat org.apache.qpid.server.logging.AbstractMessageLogger.getLogActor(AbstractMessageLogger.java:105)\n"
        "\tat org.apache.qpid.server.SystemLauncher.startup(SystemLauncher.java:198)\n"
    )
    result = classify_environment_failure(error)
    assert result is not None
    assert "Qpid Broker-J" in result
    assert "JDK 24+" in result

def test_classify_environment_failure_detects_missing_executable():
    error = "Failed to invoke mvn compile: [Errno 2] No such file or directory: 'mvn'"
    result = classify_environment_failure(error)
    assert result is not None
    assert "'mvn' was not found on PATH" in result

def test_classify_environment_failure_ignores_normal_compile_error():
    error = "COMPILATION FAILURE:\ncannot find symbol: class IgniteCache\nlocation: class App"
    assert classify_environment_failure(error) is None

def test_classify_environment_failure_ignores_filenotfound_not_from_the_toolchain_wrapper():
    # A generated app's own legitimate, code-fixable FileNotFoundError (e.g. it
    # opened a config file at the wrong path) must NOT be misclassified as a
    # toolchain problem just because the substring "No such file or directory"
    # appears somewhere in a traceback - only PolymorphicValidator's own
    # "Failed to invoke/execute ...: [Errno 2]..." wrapper (produced when the
    # launched executable itself can't be found) should match.
    error = (
        "Traceback (most recent call last):\n"
        '  File "app.py", line 3, in <module>\n'
        "    open('config.json')\n"
        "FileNotFoundError: [Errno 2] No such file or directory: 'config.json'"
    )
    assert classify_environment_failure(error) is None

def test_check_java_toolchain_mismatch_skips_non_java_stack():
    # A Python/Ruby goal must never pay for (or trigger) this check at all.
    with patch("kriya.tools.validate.check_java_toolchain") as mock_check:
        assert _check_java_toolchain_mismatch("python") is None
        mock_check.assert_not_called()

def test_check_java_toolchain_mismatch_returns_none_when_versions_match():
    with patch("kriya.tools.validate.check_java_toolchain", return_value={
        "java_found": True, "java_version": "17",
        "mvn_found": True, "mvn_java_version": "17",
        "mismatch": False,
    }):
        assert _check_java_toolchain_mismatch("java") is None

def test_check_java_toolchain_mismatch_returns_message_on_mismatch():
    with patch("kriya.tools.validate.check_java_toolchain", return_value={
        "java_found": True, "java_version": "17",
        "mvn_found": True, "mvn_java_version": "26",
        "mismatch": True,
    }):
        result = _check_java_toolchain_mismatch("java")
    assert result is not None
    assert "JDK 17" in result and "JDK 26" in result

def test_java_toolchain_fact_none_when_neither_tool_found(tmp_path):
    with patch("kriya.tools.validate.check_java_toolchain", return_value={
        "java_found": False, "java_version": None,
        "mvn_found": False, "mvn_java_version": None,
        "mismatch": False,
    }):
        assert _java_toolchain_fact("Create a Java Maven app", str(tmp_path)) is None

def test_java_toolchain_fact_prefers_mvn_version_over_java(tmp_path):
    # mvn is what a generated app's exec:java/exec:exec invocation actually
    # executes under - that's the number a JDK-version-conditional skill rule
    # needs, even if it differs from plain 'java'.
    with patch("kriya.tools.validate.check_java_toolchain", return_value={
        "java_found": True, "java_version": "17",
        "mvn_found": True, "mvn_java_version": "26",
        "mismatch": True,
    }):
        result = _java_toolchain_fact("Create a Java Maven app", str(tmp_path))
    assert result is not None
    assert "JDK 26" in result
    assert "JDK 17" not in result

def test_java_toolchain_fact_falls_back_to_java_version_when_mvn_absent(tmp_path):
    with patch("kriya.tools.validate.check_java_toolchain", return_value={
        "java_found": True, "java_version": "17",
        "mvn_found": False, "mvn_java_version": None,
        "mismatch": False,
    }):
        result = _java_toolchain_fact("Create a Java Maven app", str(tmp_path))
    assert result is not None
    assert "JDK 17" in result

def test_java_toolchain_fact_none_for_non_java_goal_even_when_tools_found(tmp_path):
    """Regression test for a real bug found live (2026-08-06 eval harness):
    _java_toolchain_fact() used to fire whenever Maven/Java were found
    ANYWHERE ON THE MACHINE, regardless of whether the current goal had
    anything to do with Java - so a Django goal's prompt got an unrelated
    "Target JVM: JDK 26" fact injected, directly contradicting the
    ecosystem-preservation invariant telling the model not to write Java in
    the very same prompt. The likely real root cause of the long-open
    "Django goal produced Java/Spring code" mystery from 2026-08-04."""
    with patch("kriya.tools.validate.check_java_toolchain", return_value={
        "java_found": True, "java_version": "17",
        "mvn_found": True, "mvn_java_version": "17",
        "mismatch": False,
    }):
        result = _java_toolchain_fact(
            "Using Django 5.2, add a minimal view at /healthz", str(tmp_path)
        )
    assert result is None

def test_goal_or_repo_targets_java_true_for_existing_pom(tmp_path):
    (tmp_path / "pom.xml").write_text("<project/>")
    assert _goal_or_repo_targets_java("Add a new endpoint", str(tmp_path)) is True

def test_goal_or_repo_targets_java_true_for_existing_gradle(tmp_path):
    (tmp_path / "build.gradle").write_text("")
    assert _goal_or_repo_targets_java("Add a new endpoint", str(tmp_path)) is True

def test_goal_or_repo_targets_java_true_for_goal_text_mention(tmp_path):
    assert _goal_or_repo_targets_java("Create a Spring Boot REST endpoint", str(tmp_path)) is True

def test_goal_or_repo_targets_java_false_for_unrelated_goal_and_fresh_workspace(tmp_path):
    assert _goal_or_repo_targets_java("Using Django 5.2, add a minimal view", str(tmp_path)) is False

_POM_WITH_SECURITY_MANAGER_FLAG = """<project>
  <build>
    <plugins>
      <plugin>
        <groupId>org.codehaus.mojo</groupId>
        <artifactId>exec-maven-plugin</artifactId>
        <configuration>
          <executable>java</executable>
          <arguments>
            <argument>-Djava.security.manager=allow</argument>
            <argument>-classpath</argument>
            <classpath/>
            <argument>${exec.mainClass}</argument>
          </arguments>
        </configuration>
      </plugin>
    </plugins>
  </build>
</project>
"""

def test_strip_jdk_incompatible_jvm_flags_strips_on_forbidden_jdk(tmp_path):
    """Regression test for a real bug found live (2026-08-07 eval harness):
    skills/qpid/rules.txt already states the correct JDK-version-conditional
    rule, and _java_toolchain_fact() already surfaces the correct JDK fact -
    active_skills confirmed both reached the model, which still added the
    forbidden flag on every attempt anyway. Deterministic correction, not
    more prompting, is the fix."""
    (tmp_path / "pom.xml").write_text(_POM_WITH_SECURITY_MANAGER_FLAG)
    with patch("kriya.tools.validate.check_java_toolchain", return_value={
        "java_found": True, "java_version": "26",
        "mvn_found": True, "mvn_java_version": "26",
        "mismatch": False,
    }):
        note = _strip_jdk_incompatible_jvm_flags(str(tmp_path))
    assert note is not None
    assert "java.security.manager" in note
    assert "JDK 26" in note
    new_content = (tmp_path / "pom.xml").read_text()
    assert "-Djava.security.manager=allow" not in new_content
    # The rest of the arguments must survive untouched.
    assert "-classpath" in new_content
    assert "${exec.mainClass}" in new_content

def test_strip_jdk_incompatible_jvm_flags_leaves_flag_on_supported_jdk(tmp_path):
    # On JDK 17-23 this flag is REQUIRED (see skills/qpid/rules.txt) - must
    # not be stripped just because it's in the known-flags table.
    (tmp_path / "pom.xml").write_text(_POM_WITH_SECURITY_MANAGER_FLAG)
    with patch("kriya.tools.validate.check_java_toolchain", return_value={
        "java_found": True, "java_version": "17",
        "mvn_found": True, "mvn_java_version": "17",
        "mismatch": False,
    }):
        note = _strip_jdk_incompatible_jvm_flags(str(tmp_path))
    assert note is None
    assert "-Djava.security.manager=allow" in (tmp_path / "pom.xml").read_text()

def test_strip_jdk_incompatible_jvm_flags_none_when_flag_absent(tmp_path):
    (tmp_path / "pom.xml").write_text("<project></project>")
    with patch("kriya.tools.validate.check_java_toolchain", return_value={
        "java_found": True, "java_version": "26",
        "mvn_found": True, "mvn_java_version": "26",
        "mismatch": False,
    }):
        assert _strip_jdk_incompatible_jvm_flags(str(tmp_path)) is None

def test_strip_jdk_incompatible_jvm_flags_none_when_no_pom(tmp_path):
    assert _strip_jdk_incompatible_jvm_flags(str(tmp_path)) is None

def test_strip_jdk_incompatible_jvm_flags_uses_override_target_not_mvn_default(tmp_path):
    """Regression test for a real bug found live (2026-08-07 eval harness,
    ignite_qpid_person re-run after the JAVA_HOME override fix): with
    java_home_override set, this function used to call check_java_toolchain()
    fresh and decide against mvn's UNMODIFIED default JDK (26 here) even
    though JAVA_HOME was already being forced onto JDK 17 for every Maven
    subprocess in this same run - two independent 'what JDK is this?'
    computations that disagreed about the one that actually mattered (the
    JDK the subprocess will really run under). When java_home_override is
    passed, the decision must be made against toolchain['java_version'] (the
    JDK actually in effect), not mvn_java_version (mvn's untouched
    default)."""
    (tmp_path / "pom.xml").write_text(_POM_WITH_SECURITY_MANAGER_FLAG)
    with patch("kriya.tools.validate.check_java_toolchain", return_value={
        "java_found": True, "java_version": "17",
        "mvn_found": True, "mvn_java_version": "26",
        "mismatch": True,
    }):
        note = _strip_jdk_incompatible_jvm_flags(
            str(tmp_path),
            java_home_override="/Library/Java/JavaVirtualMachines/temurin-17.jdk/Contents/Home",
        )
    # Effective target is JDK 17 (the override), where this flag is
    # required, not JDK 26 (mvn's untouched default) - must NOT be stripped.
    assert note is None
    assert "-Djava.security.manager=allow" in (tmp_path / "pom.xml").read_text()

def test_strip_jdk_incompatible_jvm_flags_still_strips_when_override_target_is_forbidden(tmp_path):
    """Same override-aware decision, opposite direction: when the overridden
    target itself is JDK 24+, the flag must still be stripped."""
    (tmp_path / "pom.xml").write_text(_POM_WITH_SECURITY_MANAGER_FLAG)
    with patch("kriya.tools.validate.check_java_toolchain", return_value={
        "java_found": True, "java_version": "26",
        "mvn_found": True, "mvn_java_version": "17",
        "mismatch": True,
    }):
        note = _strip_jdk_incompatible_jvm_flags(str(tmp_path), java_home_override="/some/jdk-26/Home")
    assert note is not None
    assert "JDK 26" in note
    assert "-Djava.security.manager=allow" not in (tmp_path / "pom.xml").read_text()

def test_pin_exec_plugin_executable_pins_when_override_active(tmp_path):
    """Regression test for a real bug found live, 2026-08-11
    (kriya-staged-protocol-ignite-qpid): Runtime Verification reported PASSED
    because Kriya forces JAVA_HOME onto the goal-stated target JDK for its
    OWN subprocess calls only - that override never reached the delivered
    pom.xml, which still had a bare <executable>java</executable> resolved
    via PATH at whatever JDK is default for whoever runs it later. Confirmed
    live: the exact same pom.xml crashed immediately once run outside
    Kriya's own JAVA_HOME-overridden environment, on a machine where 'mvn'
    itself defaults to a JDK the app is genuinely incompatible with."""
    (tmp_path / "pom.xml").write_text(_POM_WITH_SECURITY_MANAGER_FLAG)
    note = _pin_exec_plugin_executable_to_resolved_jdk(
        str(tmp_path),
        java_home_override="/Library/Java/JavaVirtualMachines/temurin-17.jdk/Contents/Home",
    )
    assert note is not None
    new_content = (tmp_path / "pom.xml").read_text()
    assert (
        "<executable>/Library/Java/JavaVirtualMachines/temurin-17.jdk/Contents/Home/bin/java</executable>"
        in new_content
    )
    # The rest of the config must survive untouched.
    assert "-Djava.security.manager=allow" in new_content
    assert "${exec.mainClass}" in new_content

def test_pin_exec_plugin_executable_none_without_override(tmp_path):
    # Nothing to reconcile without a detected java/mvn mismatch in the first
    # place - must leave the pom untouched.
    (tmp_path / "pom.xml").write_text(_POM_WITH_SECURITY_MANAGER_FLAG)
    assert _pin_exec_plugin_executable_to_resolved_jdk(str(tmp_path), None) is None
    assert "<executable>java</executable>" in (tmp_path / "pom.xml").read_text()

def test_pin_exec_plugin_executable_none_when_already_pinned(tmp_path):
    # Never overwrite a value the model or a prior pass already pinned
    # deliberately.
    already_pinned = _POM_WITH_SECURITY_MANAGER_FLAG.replace(
        "<executable>java</executable>",
        "<executable>/some/other/jdk/bin/java</executable>",
    )
    (tmp_path / "pom.xml").write_text(already_pinned)
    note = _pin_exec_plugin_executable_to_resolved_jdk(str(tmp_path), "/Library/Java/temurin-17")
    assert note is None
    assert "/some/other/jdk/bin/java" in (tmp_path / "pom.xml").read_text()

def test_pin_exec_plugin_executable_none_when_no_pom(tmp_path):
    assert _pin_exec_plugin_executable_to_resolved_jdk(str(tmp_path), "/Library/Java/temurin-17") is None

def test_pin_exec_plugin_executable_none_for_exec_java_shape(tmp_path):
    # exec:java always runs inside Maven's own already-started JVM and never
    # reads <executable> at all - a pom shaped for exec:java has no such tag,
    # and pinning one that isn't there would be meaningless.
    (tmp_path / "pom.xml").write_text("""<project>
      <build>
        <plugins>
          <plugin>
            <groupId>org.codehaus.mojo</groupId>
            <artifactId>exec-maven-plugin</artifactId>
            <configuration>
              <mainClass>${exec.mainClass}</mainClass>
            </configuration>
          </plugin>
        </plugins>
      </build>
    </project>
    """)
    assert _pin_exec_plugin_executable_to_resolved_jdk(str(tmp_path), "/Library/Java/temurin-17") is None

def test_resolve_jdk_home_for_version_uses_java_home_tool_on_macos():
    """Regression test for a real, live bug (2026-08-07): the ORIGINAL
    single-heuristic design derived a JDK home by walking up from 'java' on
    PATH, which on macOS is ALWAYS Apple's own dispatcher stub
    (/usr/bin/java - a real, non-symlinked, root-owned file, never a
    symlink into an actual JDK). That silently produced '/usr' as the
    "JDK home" - a directory that happened to satisfy the '.../bin/java'
    layout check but obviously isn't one - and set as JAVA_HOME, hung a
    real `mvn clean compile` subprocess indefinitely (confirmed live via
    `ps` showing it stuck with near-zero CPU time). macOS must instead use
    `/usr/libexec/java_home -v <version>`, the platform's own correct tool
    for this, which this test confirms actually gets called and its output
    used - not the fallback heuristic at all."""
    with patch("sys.platform", "darwin"), \
         patch("os.path.exists", return_value=True), \
         patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0, stdout="/Library/Java/JavaVirtualMachines/temurin-17.jdk/Contents/Home\n",
        )
        with patch("os.path.isdir", return_value=True):
            result = _resolve_jdk_home_for_version("17")
    assert result == "/Library/Java/JavaVirtualMachines/temurin-17.jdk/Contents/Home"
    assert mock_run.call_args.args[0] == ["/usr/libexec/java_home", "-v", "17"]

def test_resolve_jdk_home_for_version_none_when_java_home_tool_fails_on_macos():
    with patch("sys.platform", "darwin"), \
         patch("os.path.exists", return_value=True), \
         patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        assert _resolve_jdk_home_for_version("17") is None

def test_resolve_jdk_home_for_version_falls_back_to_bin_java_heuristic_on_linux(tmp_path):
    jdk_home = tmp_path / "jdk-17.0.10"
    bin_dir = jdk_home / "bin"
    bin_dir.mkdir(parents=True)
    java_bin = bin_dir / "java"
    java_bin.write_text("")
    with patch("sys.platform", "linux"), patch("shutil.which", return_value=str(java_bin)):
        assert _resolve_jdk_home_for_version("17") == str(jdk_home)

def test_resolve_jdk_home_for_version_none_when_java_not_found_on_linux():
    with patch("sys.platform", "linux"), patch("shutil.which", return_value=None):
        assert _resolve_jdk_home_for_version("17") is None

def test_resolve_jdk_home_for_version_none_for_unexpected_layout_on_linux(tmp_path):
    # 'java' not sitting directly under a 'bin/' directory - don't guess at
    # a JDK home from a layout that doesn't match the standard convention.
    weird_dir = tmp_path / "not_bin"
    weird_dir.mkdir()
    java_bin = weird_dir / "java"
    java_bin.write_text("")
    with patch("sys.platform", "linux"), patch("shutil.which", return_value=str(java_bin)):
        assert _resolve_jdk_home_for_version("17") is None

def test_resolve_java_home_override_none_when_no_mismatch():
    with patch("kriya.tools.validate.check_java_toolchain", return_value={
        "java_found": True, "java_version": "17",
        "mvn_found": True, "mvn_java_version": "17",
        "mismatch": False,
    }):
        assert _resolve_java_home_override("Using Java 17") is None

def test_resolve_java_home_override_none_when_goal_states_no_version():
    with patch("kriya.tools.validate.check_java_toolchain", return_value={
        "java_found": True, "java_version": "17",
        "mvn_found": True, "mvn_java_version": "26",
        "mismatch": True,
    }):
        assert _resolve_java_home_override("Create a Java Maven app") is None

def test_resolve_java_home_override_none_when_goal_matches_the_wrong_side():
    # The goal wants what 'mvn' already resolves to, not 'java' - nothing to
    # correct toward; overriding here would make things worse, not better.
    with patch("kriya.tools.validate.check_java_toolchain", return_value={
        "java_found": True, "java_version": "17",
        "mvn_found": True, "mvn_java_version": "26",
        "mismatch": True,
    }):
        assert _resolve_java_home_override("Targeting Java 26") is None

def test_resolve_java_home_override_resolves_when_goal_matches_java_side():
    """Regression test for a real, live-diagnosed gap: pom.xml's
    maven.compiler.source/target controls the Java LANGUAGE version javac
    targets, not which JDK 'mvn' itself runs under - a goal explicitly
    stating "targeting Java 17" had no way to make Maven actually honor
    that when 'mvn' defaults to a different JDK on the machine (confirmed
    live: JDK 26 broke a Qpid Broker-J API call unrelated to anything the
    generated code controls)."""
    # Patched on kriya.workflow.toolchain, not kriya.workflow.workflow - found
    # live, 2026-08-16, investigating why this test had been silently failing
    # all session as a presumed "environment-dependent" baseline item: it
    # isn't one. _resolve_java_home_override() and _resolve_jdk_home_for_version()
    # both live in kriya/workflow/toolchain.py and call each other as a
    # same-module reference - workflow.py's own re-exported copy (kept for
    # backward-compat import call sites after the 2026-08-11 modularization)
    # is a DIFFERENT name binding that _resolve_java_home_override() never
    # actually reaches, so patching it left the real, unmocked function
    # scanning THIS machine's real filesystem for a JDK 17 install and
    # finding whatever's really there instead of the intended mock value -
    # confirmed directly via a standalone repro before touching this test.
    with patch("kriya.tools.validate.check_java_toolchain", return_value={
        "java_found": True, "java_version": "17",
        "mvn_found": True, "mvn_java_version": "26",
        "mismatch": True,
    }), patch(
        "kriya.workflow.toolchain._resolve_jdk_home_for_version",
        return_value="/opt/jdk-17",
    ) as mock_resolve:
        assert _resolve_java_home_override("In a Maven project targeting Java 17") == "/opt/jdk-17"
    mock_resolve.assert_called_once_with("17")

def test_detect_missing_build_manifest_finds_pom_gap(tmp_path):
    """Regression test for a real bug found live (2026-08-07,
    kriya-protocol-parser-app): pom.xml was never created across two
    separate full runs, and nothing in the retry loop could ever recover
    it - extract_implicated_files() can't implicate a file the error text
    never names, and IncompleteGenerationError only fires for a file the
    Architect's design DID list but the Developer dropped, not one never
    requested at all."""
    error = (
        "Java compilation failed:\n"
        "/workspace/src/main/java/com/example/Main.java:5: error: package "
        "org.springframework.context does not exist\n"
        "import org.springframework.context.ApplicationContext;\n"
    )
    assert _detect_missing_build_manifest(str(tmp_path), error) == "pom.xml"

def test_detect_missing_build_manifest_none_when_pom_exists(tmp_path):
    (tmp_path / "pom.xml").write_text("<project></project>")
    error = "error: package org.junit does not exist"
    assert _detect_missing_build_manifest(str(tmp_path), error) is None

def test_detect_missing_build_manifest_none_when_gradle_exists(tmp_path):
    (tmp_path / "build.gradle").write_text("")
    error = "error: package org.junit does not exist"
    assert _detect_missing_build_manifest(str(tmp_path), error) is None

def test_detect_missing_build_manifest_none_for_unrelated_compile_error(tmp_path):
    # A real code bug (wrong type), not a missing-dependency shape - must
    # not misfire and redirect the retry loop at pom.xml for an unrelated
    # problem.
    error = "error: incompatible types: java.lang.Object cannot be converted to com.example.Person"
    assert _detect_missing_build_manifest(str(tmp_path), error) is None

@pytest.mark.asyncio
async def test_workflow_non_java_goal_prompt_omits_java_toolchain_fact_even_when_tools_found(tmp_path):
    """End-to-end regression test for the same real bug, at the point it
    actually reaches a prompt: even with real Java/Maven found on this
    machine (unmocked - relies on _java_toolchain_fact's own gating, not on
    check_java_toolchain returning nothing), a Django goal's Developer
    prompt must never contain the Environment Fact block. Confirms the fix
    holds all the way through convention_prompt -> skills_prompt ->
    existing_code_context, not just at the unit level."""
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write views.py",
        "Review: Approved",
    ])
    we = WorkflowEngine(kernel, llm)
    we.developer.run_generation = AsyncMock(return_value=[
        {"filepath": "views.py", "content": "def healthz(request):\n    return {}\n"}
    ])
    res = await we.run_generation_workflow(
        goal="Using Django 5.2, add a minimal view at /healthz", workspace_path=str(tmp_path)
    )
    assert res["quality_gates_passed"] is True
    first_call_kwargs = we.developer.run_generation.call_args_list[0].kwargs
    assert "Environment Fact" not in first_call_kwargs["existing_code_context"]
    assert "Target JVM" not in first_call_kwargs["existing_code_context"]

@pytest.mark.asyncio
async def test_workflow_java_goal_prompt_includes_java_toolchain_fact_when_tools_found(tmp_path):
    """Complementary case: a goal that genuinely does mention Java must still
    get the fact when the toolchain is actually found - the fix scopes the
    fact to real evidence, it doesn't just disable it outright."""
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write App.java",
        "Review: Approved",
    ])
    we = WorkflowEngine(kernel, llm)
    we.developer.run_generation = AsyncMock(return_value=[
        {"filepath": "App.java", "content": "public class App {}\n"}
    ])
    with patch("kriya.tools.validate.check_java_toolchain", return_value={
        "java_found": True, "java_version": "17",
        "mvn_found": True, "mvn_java_version": "17",
        "mismatch": False,
    }):
        res = await we.run_generation_workflow(
            goal="Create a Java app using Maven", workspace_path=str(tmp_path)
        )
    assert res["quality_gates_passed"] is True
    first_call_kwargs = we.developer.run_generation.call_args_list[0].kwargs
    assert "Environment Fact" in first_call_kwargs["existing_code_context"]
    assert "JDK 17" in first_call_kwargs["existing_code_context"]

@pytest.mark.asyncio
async def test_workflow_applies_java_home_override_to_maven_subprocess(tmp_path):
    """End-to-end regression test for the real, live-diagnosed gap:
    maven.compiler.source/target controls the Java LANGUAGE version javac
    targets, not which JDK 'mvn' itself runs under - a goal explicitly
    stating "targeting Java 17" had no way to make Maven actually honor
    that when 'mvn' defaults to a different JDK. Confirms
    _resolve_java_home_override()'s result actually reaches the real Maven
    subprocess call (via PolymorphicValidator.java_home_override), not just
    the decision logic in isolation - the wiring gap that would make the
    whole feature a no-op even with correct decision logic."""
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write pom.xml",
        "Review: Approved",
    ])
    we = WorkflowEngine(kernel, llm)
    we.developer.run_generation = AsyncMock(return_value=[
        {"filepath": "pom.xml", "content": "<project></project>"}
    ])
    with patch(
        "kriya.workflow.attempt._resolve_java_home_override", return_value="/opt/jdk-17",
    ), patch("subprocess.Popen") as mock_popen:
        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.communicate.return_value = ("BUILD SUCCESS", "")
        mock_popen.return_value = mock_process
        res = await we.run_generation_workflow(
            goal="Create a Java app using Maven, targeting Java 17", workspace_path=str(tmp_path)
        )

    assert res["quality_gates_passed"] is True
    # subprocess.Popen is patched process-wide, so this also catches several
    # OTHER real internal subprocess calls beyond the one this test actually
    # cares about: create_git_worktree()'s own git commands (why the worktree
    # gracefully falls back to workspace_path in this test's log output - its
    # real output never matches this mock's generic response, a known-fine
    # degrade-not-crash path), and check_java_toolchain()'s own unmocked
    # `subprocess.run(["mvn", "-version"], ...)` preflight check (which never
    # explicitly passes env=, unlike _run_cmd_with_timeout's real compile-check
    # call, so it has no "env" key in its kwargs at all - filtering on cmd[0]
    # == "mvn" alone isn't enough to land on the right call), and (2026-08-16,
    # PolymorphicValidator.run_pom_validate()) an earlier "mvn validate"
    # pre-check that ALSO goes through _run_cmd_with_timeout (so it ALSO has
    # "env" in its kwargs) but deliberately does NOT apply java_home_override -
    # that check never invokes javac, so it doesn't need the goal-specific JDK
    # targeting real compilation does (see that method's own docstring). Filter
    # to specifically the REAL compile invocation, not just "any mvn call that
    # passed env=".
    mvn_calls = [
        c for c in mock_popen.call_args_list
        if c.args and c.args[0] and c.args[0][0] == "mvn" and "env" in c.kwargs and "compile" in c.args[0]
    ]
    assert mvn_calls, mock_popen.call_args_list
    _, kwargs = mvn_calls[0]
    assert kwargs["env"]["JAVA_HOME"] == "/opt/jdk-17"

@pytest.mark.asyncio
async def test_approve_web_lookup_true_when_auto_approve_set():
    cfg = AppConfig()
    cfg.autonomy.web_lookup_auto_approve = True
    we = WorkflowEngine(Kernel(config=cfg), LLMClient(cfg))
    # No callback needed at all - the config opt-in alone is sufficient.
    assert await we._approve_web_lookup(["ignite"], "http://fake-search:8080", None) is True

@pytest.mark.asyncio
async def test_approve_web_lookup_fails_closed_with_no_callback_and_no_opt_in():
    cfg = AppConfig()
    we = WorkflowEngine(Kernel(config=cfg), LLMClient(cfg))
    assert await we._approve_web_lookup(["ignite"], "http://fake-search:8080", None) is False

@pytest.mark.asyncio
async def test_approve_web_lookup_invokes_callback_with_exact_terms_and_url():
    cfg = AppConfig()
    we = WorkflowEngine(Kernel(config=cfg), LLMClient(cfg))
    received = {}

    def callback(terms, base_url):
        received["terms"] = terms
        received["base_url"] = base_url
        return True

    result = await we._approve_web_lookup(["org.apache.ignite:ignite-core"], "http://fake-search:8080", callback)
    assert result is True
    assert received == {"terms": ["org.apache.ignite:ignite-core"], "base_url": "http://fake-search:8080"}

@pytest.mark.asyncio
async def test_approve_web_lookup_respects_declined_callback():
    cfg = AppConfig()
    we = WorkflowEngine(Kernel(config=cfg), LLMClient(cfg))
    assert await we._approve_web_lookup(["ignite"], "http://fake-search:8080", lambda terms, url: False) is False

@pytest.mark.asyncio
async def test_approve_web_lookup_supports_async_callback():
    cfg = AppConfig()
    we = WorkflowEngine(Kernel(config=cfg), LLMClient(cfg))

    async def callback(terms, base_url):
        return True

    assert await we._approve_web_lookup(["ignite"], "http://fake-search:8080", callback) is True

@pytest.mark.asyncio
async def test_approve_web_lookup_fails_closed_when_callback_raises():
    cfg = AppConfig()
    we = WorkflowEngine(Kernel(config=cfg), LLMClient(cfg))

    def callback(terms, base_url):
        raise RuntimeError("boom")

    assert await we._approve_web_lookup(["ignite"], "http://fake-search:8080", callback) is False

@pytest.mark.asyncio
async def test_augment_error_with_live_lookup_no_terms_never_searches():
    with patch("kriya.tools.search.search_web", new=AsyncMock()) as mock_search:
        result = await _augment_error_with_live_lookup("some error", [], "http://fake-search:8080", 3)
    assert result == "some error"
    mock_search.assert_not_called()

@pytest.mark.asyncio
async def test_augment_error_with_live_lookup_appends_found_content():
    found = [{"term": "org.codehaus.mojo:exec-maven-plugin", "url": "https://example.com/exec-plugin", "snippet": "..."}]
    with patch("kriya.tools.search.search_web", new=AsyncMock(return_value=found)), \
         patch("kriya.tools.web.fetch_url_text", new=AsyncMock(return_value="Remove the <arguments> block.")):
        result = await _augment_error_with_live_lookup(
            "COMPILATION FAILURE: ...", ["org.codehaus.mojo:exec-maven-plugin"], "http://fake-search:8080", 3
        )
    assert "COMPILATION FAILURE: ..." in result
    assert "Reference material found for 'org.codehaus.mojo:exec-maven-plugin'" in result
    assert "Remove the <arguments> block." in result

@pytest.mark.asyncio
async def test_augment_error_with_live_lookup_nothing_found_returns_unchanged():
    with patch("kriya.tools.search.search_web", new=AsyncMock(return_value=[])):
        result = await _augment_error_with_live_lookup(
            "some error", ["org.codehaus.mojo:exec-maven-plugin"], "http://fake-search:8080", 3
        )
    assert result == "some error"


@pytest.mark.asyncio
async def test_augment_error_with_live_lookup_skips_a_thin_landing_page_candidate():
    """Regression test for a finding from the 2026-08-12 SME review: this
    function always used candidates[0]'s text, even though
    _resolve_via_web_lookup fetches MULTIPLE candidates per term specifically
    to survive an unhelpful top result (the "landing-page problem" its own
    docstring documents) - the sibling skill-gap lookup path already tries
    each candidate in turn (_extract_first_usable), this one never did."""
    found = [
        {"url": "https://example.com/landing", "snippet": "..."},
        {"url": "https://example.com/real-example", "snippet": "..."},
    ]

    async def fake_fetch(url, quiet_on_failure=False):
        if url == "https://example.com/landing":
            return "Welcome to our project."  # thin, below the usable threshold
        return "Full working example: " + ("x" * 300)

    with patch("kriya.tools.search.search_web", new=AsyncMock(return_value=found)), \
         patch("kriya.tools.web.fetch_url_text", new=AsyncMock(side_effect=fake_fetch)):
        result = await _augment_error_with_live_lookup(
            "COMPILATION FAILURE: ...", ["org.codehaus.mojo:exec-maven-plugin"], "http://fake-search:8080", 3
        )

    assert "https://example.com/real-example" in result
    assert "Full working example" in result
    assert "Welcome to our project." not in result


@pytest.mark.asyncio
async def test_augment_error_with_live_lookup_falls_back_to_first_candidate_when_none_are_usable():
    found = [
        {"url": "https://example.com/thin1", "snippet": "..."},
        {"url": "https://example.com/thin2", "snippet": "..."},
    ]
    with patch("kriya.tools.search.search_web", new=AsyncMock(return_value=found)), \
         patch("kriya.tools.web.fetch_url_text", new=AsyncMock(return_value="too short")):
        result = await _augment_error_with_live_lookup(
            "some error", ["org.codehaus.mojo:exec-maven-plugin"], "http://fake-search:8080", 3
        )
    assert "https://example.com/thin1" in result
    assert "too short" in result


@pytest.mark.asyncio
async def test_workflow_error_triggered_live_lookup_on_repeated_compile_failure(tmp_path):
    """A compile failure that repeats identically across two consecutive Developer
    retry attempts (the model isn't self-correcting) should trigger live lookup,
    folding found reference material into the THIRD attempt's prompt - not the
    first attempt (no repeat yet), and search must fire exactly once."""
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.autonomy.web_lookup_enabled = True
    cfg.autonomy.web_lookup_auto_approve = True  # bypass the pre-send confirmation gate for this test
    cfg.search.base_url = "http://fake-search:8080"
    cfg.paths.skills = str(tmp_path / "skills")
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    repeated_error = (
        "Failed to execute goal org.codehaus.mojo:exec-maven-plugin:3.1.0:java "
        "for parameter arguments: Cannot store value into array"
    )

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write App.java",
        '[{"filepath": "App.java", "content": "class App {}"}]',  # attempt 1 - fails
        '[{"filepath": "App.java", "content": "class App {}"}]',  # attempt 2 - fails identically (repeat)
        '[{"filepath": "App.java", "content": "class App {}"}]',  # attempt 3 - succeeds
        "Two full-set retries were needed - lesson extraction.",
        "Review: Approved",
    ])

    found = [{"term": "org.codehaus.mojo:exec-maven-plugin", "url": "https://example.com/exec-plugin", "snippet": "..."}]

    we = WorkflowEngine(kernel, llm)
    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check") as mock_compile, \
         patch("kriya.tools.search.search_web", new=AsyncMock(return_value=found)) as mock_search, \
         patch("kriya.tools.web.fetch_url_text", new=AsyncMock(return_value="Remove the <arguments> block - exec:java doesn't need it.")):
        mock_compile.side_effect = [
            {"success": False, "output": repeated_error},
            {"success": False, "output": repeated_error},
            {"success": True, "output": ""},
        ]
        res = await we.run_generation_workflow(goal="Create a Java app", workspace_path=str(tmp_path))

    assert res["quality_gates_passed"] is True
    mock_search.assert_called_once_with(
        "org.codehaus.mojo:exec-maven-plugin example", "http://fake-search:8080", top_k=3
    )

@pytest.mark.asyncio
async def test_workflow_repeated_failure_live_lookup_resolves_wrong_import_via_dependency_match(tmp_path):
    """Regression test for a real bug found live during M3 golden-use-case
    validation: a wrong-import-path compile failure (e.g. IgniteCache
    imported from org.apache.ignite.cache instead of the real top-level
    org.apache.ignite package) has no groupId:artifactId coordinate in it at
    all, so the repeated-failure live-lookup trigger previously had nothing
    to search for no matter how many times the SAME failure recurred - this
    was confirmed live, not just reasoned about (the exact same IgniteCache
    mistake recurred across all 7 retry attempts of a real M3 run with live
    lookup enabled, and it never once engaged). Proves the fix end-to-end:
    once the erroneous package is cross-checked against the project's real
    declared dependencies, a search DOES fire, scoped to that dependency's
    coordinate plus the symbol name."""
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.autonomy.web_lookup_enabled = True
    cfg.autonomy.web_lookup_auto_approve = True
    cfg.search.base_url = "http://fake-search:8080"
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    repeated_error = (
        "[ERROR] .../IntegrationApp.java:[5,31] cannot find symbol\n"
        "[ERROR]   symbol:   class IgniteCache\n"
        "[ERROR]   location: package org.apache.ignite.cache\n"
    )

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write App.java",
        '[{"filepath": "App.java", "content": "class App {}"}]',
        '[{"filepath": "App.java", "content": "class App {}"}]',
        '[{"filepath": "App.java", "content": "class App {}"}]',
        "Review: Approved",
    ])

    found = [{"term": "org.apache.ignite:ignite-core IgniteCache", "url": "https://example.com/ignite-quickstart", "snippet": "..."}]

    we = WorkflowEngine(kernel, llm)
    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check") as mock_compile, \
         patch("kriya.tools.search.search_web", new=AsyncMock(return_value=found)) as mock_search, \
         patch("kriya.tools.web.fetch_url_text", new=AsyncMock(return_value="import org.apache.ignite.IgniteCache;")), \
         patch("kriya.tools.validate.get_pom_own_coordinate", return_value=None), \
         patch("kriya.tools.validate.get_pom_dependencies", return_value=["org.apache.ignite:ignite-core"]):
        mock_compile.side_effect = [
            {"success": False, "output": repeated_error},
            {"success": False, "output": repeated_error},
            {"success": True, "output": ""},
        ]
        res = await we.run_generation_workflow(goal="Create a Java app", workspace_path=str(tmp_path))

    assert res["quality_gates_passed"] is True
    mock_search.assert_called_once_with(
        "org.apache.ignite:ignite-core IgniteCache example", "http://fake-search:8080", top_k=3
    )

@pytest.mark.asyncio
async def test_workflow_repeated_failure_live_lookup_excludes_own_project_coordinate(tmp_path):
    """Regression test for a real bug found live during golden-use-case
    validation: Maven's own build banner prints the PROJECT'S OWN
    groupId:artifactId on every single build - a repeated-failure live-lookup
    recovery attempt must not waste its shot searching for the project's own
    made-up artifact ID instead of a genuine external coordinate also present
    in the same error text."""
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.autonomy.web_lookup_enabled = True
    cfg.autonomy.web_lookup_auto_approve = True
    cfg.search.base_url = "http://fake-search:8080"
    cfg.paths.skills = str(tmp_path / "skills")
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    repeated_error = (
        "[INFO] ----------------< com.example:ignite-qpid-integration >-----------------\n"
        "[INFO] Building ignite-qpid-integration 1.0-SNAPSHOT\n"
        "[ERROR] Failed to execute goal org.codehaus.mojo:exec-maven-plugin:3.1.0:java "
        "for parameter arguments: Cannot store value into array"
    )

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write App.java",
        '[{"filepath": "App.java", "content": "class App {}"}]',
        '[{"filepath": "App.java", "content": "class App {}"}]',
        '[{"filepath": "App.java", "content": "class App {}"}]',
        "Two full-set retries were needed - lesson extraction.",
        "Review: Approved",
    ])

    found = [{"term": "org.codehaus.mojo:exec-maven-plugin", "url": "https://example.com/exec-plugin", "snippet": "..."}]

    we = WorkflowEngine(kernel, llm)
    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check") as mock_compile, \
         patch("kriya.tools.search.search_web", new=AsyncMock(return_value=found)) as mock_search, \
         patch("kriya.tools.web.fetch_url_text", new=AsyncMock(return_value="Remove the <arguments> block.")), \
         patch("kriya.tools.validate.get_pom_own_coordinate", return_value="com.example:ignite-qpid-integration"):
        mock_compile.side_effect = [
            {"success": False, "output": repeated_error},
            {"success": False, "output": repeated_error},
            {"success": True, "output": ""},
        ]
        res = await we.run_generation_workflow(goal="Create a Java app", workspace_path=str(tmp_path))

    assert res["quality_gates_passed"] is True
    mock_search.assert_called_once_with(
        "org.codehaus.mojo:exec-maven-plugin example", "http://fake-search:8080", top_k=3
    )

    third_attempt_prompt = llm.complete.await_args_list[4].args[1]
    assert "Reference material found for 'org.codehaus.mojo:exec-maven-plugin'" in third_attempt_prompt
    assert "Remove the <arguments> block" in third_attempt_prompt

    # And the first (non-repeat) failure must not have triggered anything - the
    # second Developer call's prompt has the raw error but no lookup content yet.
    second_attempt_prompt = llm.complete.await_args_list[3].args[1]
    assert "Reference material found" not in second_attempt_prompt

@pytest.mark.asyncio
async def test_workflow_error_triggered_live_lookup_disabled_by_default_never_searches(tmp_path):
    """Same repeated-failure scenario, but web_lookup_enabled left at its default
    (False) - must never call search_web, even though the failure repeats and
    contains an extractable coordinate."""
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    # web_lookup_enabled left False (default) - no search.base_url either.
    cfg.paths.skills = str(tmp_path / "skills")
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    repeated_error = "Failed to execute goal org.codehaus.mojo:exec-maven-plugin:3.1.0:java"

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write App.java",
        '[{"filepath": "App.java", "content": "class App {}"}]',
        '[{"filepath": "App.java", "content": "class App {}"}]',
        '[{"filepath": "App.java", "content": "class App {}"}]',
        "Two full-set retries were needed - lesson extraction.",
        "Review: Approved",
    ])

    we = WorkflowEngine(kernel, llm)
    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check") as mock_compile, \
         patch("kriya.tools.search.search_web", new=AsyncMock()) as mock_search:
        mock_compile.side_effect = [
            {"success": False, "output": repeated_error},
            {"success": False, "output": repeated_error},
            {"success": True, "output": ""},
        ]
        res = await we.run_generation_workflow(goal="Create a Java app", workspace_path=str(tmp_path))

    assert res["quality_gates_passed"] is True
    mock_search.assert_not_called()


def test_extract_implicated_files_matches_basename_in_error():
    error = "path/to/CacheAndMessagingClient.java:[22,13] cannot find symbol"
    known = ["src/main/java/com/example/CacheAndMessagingClient.java", "pom.xml"]
    assert extract_implicated_files(error, known) == ["src/main/java/com/example/CacheAndMessagingClient.java"]

def test_extract_implicated_files_matches_multiple():
    error = "App.java:[5,1] error, and also BrokerServer.java:[10,2] error"
    known = ["src/App.java", "src/BrokerServer.java", "pom.xml"]
    assert extract_implicated_files(error, known) == ["src/App.java", "src/BrokerServer.java"]

def test_extract_implicated_files_empty_when_no_known_file_named():
    error = "Process exited with code 1. No further details available."
    known = ["App.java", "pom.xml"]
    assert extract_implicated_files(error, known) == []

def test_extract_implicated_files_matches_full_relative_path_too():
    error = "Traceback: File \"src/main/py/app.py\", line 3, in <module>"
    known = ["src/main/py/app.py"]
    assert extract_implicated_files(error, known) == ["src/main/py/app.py"]

def test_extract_implicated_files_prefers_locator_over_boilerplate_pom_mention():
    """Regression test for a real live bug, 2026-08-10 (ignite_qpid_protocol,
    run 20260810-111517): Maven's OWN reactor startup banner
    ("[INFO]   from pom.xml") appears in the captured output of EVERY Maven
    build, success or failure, regardless of what actually broke - so the old
    plain-substring check unconditionally implicated pom.xml on every retry
    for any Maven Java goal. Confirmed live: a targeted retry burned a full
    extra per-file completion call on "Developer fix analysis for 'pom.xml'"
    correctly diagnosing an unrelated compile error in a completely different
    file, then regenerating pom.xml with nothing useful to change. A file
    named alongside a real file:line locator (ProtocolApp.java here) is now
    preferred over a bare filename mention with no locator (pom.xml, whose
    only appearance is Maven's own banner line, never a locator)."""
    error = (
        "[INFO] Building ignite-qpid-protocol 1.0-SNAPSHOT\n"
        "[INFO]   from pom.xml\n"
        "[ERROR] /worktree/src/main/java/com/example/ProtocolApp.java:[5,31] cannot find symbol\n"
        "  symbol:   class IgniteCache\n"
        "  location: package org.apache.ignite.cache\n"
    )
    known = ["src/main/java/com/example/ProtocolApp.java", "src/main/java/com/example/ProtocolParser.java", "pom.xml"]
    assert extract_implicated_files(error, known) == ["src/main/java/com/example/ProtocolApp.java"]

def test_extract_implicated_files_locator_preference_keeps_multiple_real_matches():
    # Same real run, a different attempt: a JVM stack trace gives BOTH
    # ProtocolParser.java and ProtocolApp.java their own real locators
    # alongside pom.xml's same boilerplate banner mention - both genuinely
    # implicated files must still both be returned, only pom.xml dropped.
    error = (
        "[INFO]   from pom.xml\n"
        "Exception in thread \"main\" java.nio.BufferOverflowException\n"
        "\tat com.example.ProtocolParser.encode(ProtocolParser.java:21)\n"
        "\tat com.example.ProtocolApp.main(ProtocolApp.java:37)\n"
    )
    known = ["src/main/java/com/example/ProtocolApp.java", "src/main/java/com/example/ProtocolParser.java", "pom.xml"]
    result = extract_implicated_files(error, known)
    assert set(result) == {"src/main/java/com/example/ProtocolApp.java", "src/main/java/com/example/ProtocolParser.java"}
    assert "pom.xml" not in result

def test_extract_implicated_files_still_implicates_pom_xml_when_genuinely_the_cause():
    # A real pom.xml-specific failure (no other file's locator competing)
    # must still correctly implicate pom.xml - this fix narrows the result
    # only when locator evidence points elsewhere, it never blanket-excludes
    # pom.xml.
    error = "Non-resolvable parent POM: Could not find artifact ... in pom.xml"
    known = ["src/App.java", "pom.xml"]
    assert extract_implicated_files(error, known) == ["pom.xml"]

def test_extract_implicated_files_ignores_pom_mention_thats_only_in_info_banner_noise():
    """Regression test for a real live bug, 2026-08-12 (ignite_qpid_protocol): a
    Runtime Verification hang (an unclosed Ignite resource - the actual bug is a
    missing ignite.close() in the main entrypoint's finally block) produces NO
    compile-style locator anywhere, so the 2026-08-10 "prefer a locator" fix
    (test_extract_implicated_files_prefers_locator_over_boilerplate_pom_mention
    above) never engages, and the plain substring fallback matched "pom.xml" via
    Maven's own unconditional "[INFO]   from pom.xml" banner line alone - on every
    targeted retry of a real run, even though the Developer's own fix-analysis
    correctly diagnosed the true broken file each time. With no other known file
    mentioned anywhere either, this must now return [] (triggering the existing
    full-file-set fallback) rather than wrongly implicating pom.xml."""
    error = (
        "[INFO] Scanning for projects...\n"
        "[INFO] ------------------------< com.example:ignite-qpid-protocol >------------------------\n"
        "[INFO] Building ignite-qpid-protocol 1.0-SNAPSHOT\n"
        "[INFO]   from pom.xml\n"
        "RUNTIME VERIFICATION FAILURE: The goal's described output WAS produced correctly, "
        "but the process never exited on its own and had to be killed after 90s. Fix the "
        "resource lifecycle, not the application logic, which already works.\n"
    )
    known = ["src/main/java/com/example/ProtocolApp.java", "src/main/java/com/example/ProtocolParser.java", "pom.xml"]
    assert extract_implicated_files(error, known) == []

def test_extract_implicated_files_still_matches_pom_xml_error_line_even_with_info_banner_present():
    # The [INFO] banner noise and a genuine, non-[INFO]-prefixed pom.xml error
    # (Maven's real "[ERROR] The project ... (/path/to/pom.xml) has N errors"
    # shape) can coexist in the same captured output - the real error line must
    # still win even though the banner line above it gets stripped.
    error = (
        "[INFO]   from pom.xml\n"
        "[FATAL] 'modelVersion' is missing. @ line 1, column 10\n"
        "[ERROR]   The project [unknown-group-id]:artemis-server:[unknown-version] "
        "(/private/tmp/workspace/pom.xml) has 3 errors\n"
    )
    known = ["src/App.java", "pom.xml"]
    assert extract_implicated_files(error, known) == ["pom.xml"]

def test_build_targeted_retry_prompt_frames_target_and_reference_files(tmp_path):
    (tmp_path / "App.java").write_text("class App { /* broken */ }")
    (tmp_path / "Helper.java").write_text("class Helper { /* fine */ }")

    task_desc, context = _build_targeted_retry_prompt(
        goal="Build the app",
        plan="Fix App.java",
        error_context="cannot find symbol in App.java",
        target_files=["App.java"],
        all_files_written=["App.java", "Helper.java"],
        worktree_path=str(tmp_path),
        active_code_context="=== base RAG context ===\n",
    )

    assert "TARGETED fix attempt" in task_desc
    assert "App.java" in task_desc
    assert "cannot find symbol in App.java" in task_desc
    assert "=== base RAG context ===" in context
    assert "File to fix: App.java" in context
    assert "class App { /* broken */ }" in context
    assert "already correct, reference only" in context
    assert "Helper.java" in context
    assert "class Helper { /* fine */ }" in context


@pytest.mark.asyncio
async def test_workflow_targeted_retry_fixes_implicated_file_without_escalating(tmp_path):
    """A compile failure that names a known file should trigger a targeted retry
    on the very next attempt (not a full-file-set regeneration), using the
    primary model even with a fallback chain configured - and the targeted
    attempt's prompt should show the model its own previous (broken) content,
    which the full-set path never does."""
    from kriya.config import FallbackModelConfig
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.llm_chain = [FallbackModelConfig(model="fallback-1")]
    cfg.paths.skills = str(tmp_path / "skills")
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    prompts_seen = []

    async def mock_complete(*args, **kwargs):
        prompts_seen.append((args[1] if len(args) > 1 else None, kwargs.get("model_override")))
        n = len(prompts_seen)
        if n == 1:
            return "Step 1: Write code"
        elif n == 2:
            return "Design: Write math.py"
        elif n == 3:
            # Attempt 1 (retry_count == 0): design's "math.py" mention activates
            # known_target_files, a plain per-file content completion (broken).
            return "def add(a,b)\n    return a+b"
        elif n == 4:
            # Targeted retry (implicated file), also known_target_files - plain
            # per-file content completion (fixed this time).
            return "def add(a,b):\n    return a+b"
        else:
            return "Review: Approved"

    llm.complete = mock_complete

    we = WorkflowEngine(kernel, llm)
    res = await we.run_generation_workflow(goal="Create math library", workspace_path=str(tmp_path))

    assert res["quality_gates_passed"] is True
    # Attempt 2 (index 3 in prompts_seen) must be the targeted retry: primary
    # model (no override, despite a configured fallback chain) and a prompt that
    # shows the model its own broken previous content, not just the error text.
    targeted_prompt, targeted_model_override = prompts_seen[3]
    assert targeted_model_override is None
    assert "TARGETED fix attempt" in targeted_prompt
    assert "def add(a,b)" in targeted_prompt  # the actual previous (broken) content

@pytest.mark.asyncio
async def test_workflow_no_targeted_retry_when_error_names_no_known_file(tmp_path):
    """An error that doesn't mention any known file must never trigger a
    targeted attempt - it should escalate through the full-set fallback chain
    exactly as before this feature existed."""
    from kriya.config import FallbackModelConfig
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.llm_chain = [FallbackModelConfig(model="fallback-1")]
    cfg.paths.skills = str(tmp_path / "skills")
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    model_overrides = []

    async def mock_complete(*args, **kwargs):
        model_overrides.append(kwargs.get("model_override"))
        n = len(model_overrides)
        if n == 1:
            return "Step 1: Write code"
        elif n == 2:
            return "Design: Write math.py"
        elif n in (3, 4):
            return '[{"filepath": "math.py", "content": "def add(a,b):\\n    return a+b"}]'
        else:
            return "Review: Approved"

    llm.complete = mock_complete

    we = WorkflowEngine(kernel, llm)
    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check") as mock_compile:
        mock_compile.side_effect = [
            {"success": False, "output": "Process exited with code 1. No file information available."},
            {"success": True, "output": ""},
        ]
        res = await we.run_generation_workflow(goal="Create math library", workspace_path=str(tmp_path))

    assert res["quality_gates_passed"] is True
    # Attempt 2 must escalate to the fallback chain (full-set path), never a
    # targeted (primary-model) attempt, since no file was implicated.
    assert model_overrides[3] == "fallback-1"

@pytest.mark.asyncio
async def test_workflow_prompt_includes_self_consistency_nudge_on_full_set_retry(tmp_path):
    """extra_fix_instruction (DeveloperAgent.SELF_CONSISTENCY_NUDGE) must reach
    a full-set retry too, not just a targeted one - the retry loop's third
    run_generation() call site (kriya/workflow/workflow.py), used whenever no
    known file is implicated by the error text. Wired to always-on 2026-08-10
    once spikes/fix_alignment/'s first real batch supported it."""
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write App.py",
        "Review: Approved",
    ])
    we = WorkflowEngine(kernel, llm)
    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check") as mock_compile:
        mock_compile.side_effect = [
            {"success": False, "output": "Process exited with code 1. No file information available."},
            {"success": True, "output": ""},
        ]
        we.developer.run_generation = AsyncMock(side_effect=[
            [{"filepath": "App.py", "content": "def main():\n    pass\n"}],
            [{"filepath": "App.py", "content": "def main():\n    return 0\n"}],
        ])
        res = await we.run_generation_workflow(goal="Write a script", workspace_path=str(tmp_path))
    assert res["quality_gates_passed"] is True
    second_call_kwargs = we.developer.run_generation.call_args_list[1].kwargs
    assert second_call_kwargs["extra_fix_instruction"] == DeveloperAgent.SELF_CONSISTENCY_NUDGE

@pytest.mark.asyncio
async def test_workflow_success_via_targeted_attempt_after_full_set_budget_exhausted(tmp_path):
    """Regression test for a real bug caught while implementing this: quality_passed
    used to be computed as `retry_count < max_retries`, which is wrong once a run
    can succeed via a targeted attempt AFTER the full-set budget is already
    exhausted - this must still report success correctly."""
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    # No llm_chain configured -> max_retries defaults to 4.
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write math.py",
        "Review: Approved",
    ])

    we = WorkflowEngine(kernel, llm)
    same_error = "Failed to build math.py"
    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check") as mock_compile:
        # 4 full-set failures (exhausts the default max_retries=4 full-set budget,
        # each one naming math.py so a targeted attempt becomes eligible), then a
        # 5th, targeted attempt succeeds.
        mock_compile.side_effect = [
            {"success": False, "output": same_error},
            {"success": False, "output": same_error},
            {"success": False, "output": same_error},
            {"success": False, "output": same_error},
            {"success": True, "output": ""},
        ]
        # Every Developer call (1 full-set + 3 targeted + 1 more targeted) returns
        # the same file - content doesn't matter here since run_compile_check is
        # mocked directly.
        we.developer.run_generation = AsyncMock(
            return_value=[{"filepath": "math.py", "content": "def add(a,b): return a+b"}]
        )
        res = await we.run_generation_workflow(goal="Create math library", workspace_path=str(tmp_path))

    assert res["quality_gates_passed"] is True

@pytest.mark.asyncio
async def test_workflow_passes_prior_error_context_to_developer_only_on_retries(tmp_path):
    """Regression test for a real, generalizable bug found live during golden-
    use-case validation: the Developer agent's single-shot, non-reasoning
    completion regenerated byte-for-byte identical broken code across 7
    straight retry attempts of a real failing run, despite the exact compile
    error being present in every prompt - nothing forced it to actually engage
    with the stated error before writing code. The retry loop must pass the
    real prior error text through to DeveloperAgent.run_generation on every
    retry (targeted and full-set), and explicitly None on the clean first
    attempt, so the fix-analysis step (see test_agents.py) only ever applies
    when there's a real error to analyze."""
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write math.py",
        "Review: Approved",
    ])

    we = WorkflowEngine(kernel, llm)
    same_error = "Failed to build math.py"
    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check") as mock_compile:
        mock_compile.side_effect = [
            {"success": False, "output": same_error},
            {"success": True, "output": ""},
        ]
        we.developer.run_generation = AsyncMock(
            return_value=[{"filepath": "math.py", "content": "def add(a,b): return a+b"}]
        )
        res = await we.run_generation_workflow(goal="Create math library", workspace_path=str(tmp_path))

    assert res["quality_gates_passed"] is True
    first_call_kwargs = we.developer.run_generation.call_args_list[0].kwargs
    second_call_kwargs = we.developer.run_generation.call_args_list[1].kwargs
    assert first_call_kwargs["prior_error_context"] is None
    assert same_error in second_call_kwargs["prior_error_context"]
    # implicated_files must also be None on the clean first attempt, and
    # correctly scoped to math.py (the only file the error text names) on
    # the retry - not left unset/always-None, which would silently defeat
    # the DeveloperAgent-side scoping fix even though prior_error_context
    # itself was passed correctly.
    assert first_call_kwargs["implicated_files"] is None
    assert second_call_kwargs["implicated_files"] == ["math.py"]

@pytest.mark.asyncio
async def test_workflow_passes_configured_retry_temperature_to_developer(tmp_path):
    """cfg.llm.retry_temperature must reach DeveloperAgent.run_generation on
    every call (its own scoping to the implicated file is DeveloperAgent's
    job, tested in test_agents.py) - unset (None) by default, so this is
    strictly opt-in and never changes behavior for a project that doesn't
    configure it."""
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.llm.retry_temperature = 0.05
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write math.py",
        "Review: Approved",
    ])

    we = WorkflowEngine(kernel, llm)
    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check") as mock_compile:
        mock_compile.side_effect = [
            {"success": False, "output": "Failed to build math.py"},
            {"success": True, "output": ""},
        ]
        we.developer.run_generation = AsyncMock(
            return_value=[{"filepath": "math.py", "content": "def add(a,b): return a+b"}]
        )
        res = await we.run_generation_workflow(goal="Create math library", workspace_path=str(tmp_path))

    assert res["quality_gates_passed"] is True
    for call in we.developer.run_generation.call_args_list:
        assert call.kwargs["retry_temperature"] == 0.05

@pytest.mark.asyncio
async def test_workflow_passes_error_source_context_scoped_to_implicated_file(tmp_path):
    """Regression test for a real bug found live during golden-use-case
    validation: without this, a full-set retry's per-file fix-analysis
    instruction (and the source-line context meant to accompany it) got
    broadcast to every file in the batch, not just the one the compile error
    actually named. Confirms the retry loop reads the real broken line from
    the worktree and threads it through scoped to exactly the implicated
    file."""
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.paths.logs = str(tmp_path / "logs")
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write App.java",
        "Review: Approved",
    ])

    we = WorkflowEngine(kernel, llm)
    error_with_location = (
        "[ERROR] .../App.java:[3,5] incompatible types: java.lang.Object cannot be converted to java.lang.String"
    )
    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check") as mock_compile:
        mock_compile.side_effect = [
            {"success": False, "output": error_with_location},
            {"success": True, "output": ""},
        ]
        we.developer.run_generation = AsyncMock(
            side_effect=[
                [{"filepath": "App.java", "content": "class App {\n  Object x;\n  String s = x;\n}"}],
                [{"filepath": "App.java", "content": "class App {\n  Object x;\n  String s = (String) x;\n}"}],
            ]
        )
        res = await we.run_generation_workflow(goal="Create a Java app", workspace_path=str(tmp_path))

    assert res["quality_gates_passed"] is True
    second_call_kwargs = we.developer.run_generation.call_args_list[1].kwargs
    source_context = second_call_kwargs["error_source_context"]
    assert source_context is not None
    assert "App.java" in source_context
    assert ">> 3:" in source_context["App.java"]

@pytest.mark.asyncio
async def test_workflow_passes_error_source_context_for_junit_stack_trace_test_failure(tmp_path):
    """Regression test for a real bug found live (2026-08-07,
    kriya-protocol-parser-app): unlike a compile error, a JUnit/JVM stack
    trace's own file:line locator wasn't recognized at all before this
    fix, so a targeted-test failure with a real stack trace got zero
    source-line grounding threaded to the retry - confirms it now does,
    the same way a compile failure already did. Writes a CalcTest.java
    alongside Calc.java so extract_target_test() finds a real target and
    this exercises the same TARGETED-test gate the live bug hit (not the
    separate full-regression gate, which fires when no test file can be
    identified at all)."""
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.paths.logs = str(tmp_path / "logs")
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write Calc.java and CalcTest.java",
        "Review: Approved",
    ])
    we = WorkflowEngine(kernel, llm)
    stack_trace_error = (
        "java.lang.ArithmeticException: / by zero\n"
        "\tat com.example.Calc.divide(Calc.java:8)\n"
        "\tat com.example.CalcTest.testDivide(CalcTest.java:12)\n"
    )
    # Line 8 must actually BE the divide statement, matching the stack
    # trace's claimed location - _build_error_source_context() requires
    # the reported line number to exist in the real file.
    calc_java = "\n".join([
        "package com.example;",
        "",
        "public class Calc {",
        "",
        "  int divide(int a, int b) {",
        "",
        "",
        "    return a / b;",
        "  }",
        "}",
    ])
    calc_test_java = "\n".join([
        "package com.example;",
        "",
        "public class CalcTest {",
        "",
        "  void testDivide() {",
        "",
        "",
        "",
        "",
        "",
        "",
        "    Calc.divide(1, 0);",
        "  }",
        "}",
    ])
    with patch(
        "kriya.tools.validate.PolymorphicValidator.run_compile_check",
        return_value={"success": True, "output": ""},
    ), patch("kriya.tools.validate.PolymorphicValidator.run_tests") as mock_tests:
        mock_tests.side_effect = [
            {"success": False, "output": stack_trace_error},  # attempt 1: targeted test fails
            {"success": True, "output": ""},                  # attempt 2: targeted test passes
            {"success": True, "output": ""},                  # attempt 2: full regression suite (always runs after)
        ]
        we.developer.run_generation = AsyncMock(side_effect=[
            [
                {"filepath": "Calc.java", "content": calc_java},
                {"filepath": "CalcTest.java", "content": calc_test_java},
            ],
            [
                {"filepath": "Calc.java", "content": calc_java.replace("return a / b;", "return b == 0 ? 0 : a / b;")},
                {"filepath": "CalcTest.java", "content": calc_test_java},
            ],
        ])
        res = await we.run_generation_workflow(goal="Create a Java app", workspace_path=str(tmp_path))

    assert res["quality_gates_passed"] is True
    second_call_kwargs = we.developer.run_generation.call_args_list[1].kwargs
    source_context = second_call_kwargs["error_source_context"]
    assert source_context is not None
    assert "Calc.java" in source_context
    assert ">> 8:" in source_context["Calc.java"]


@pytest.mark.asyncio
async def test_workflow_anchored_edit_failure_captures_filepath(tmp_path):
    """Regression test for a real gap found this session: apply_anchored_edits()
    (workflow.py) never received a filepath, so a failed anchor match never named
    one either - this failure class always fell through to a blind full-set retry,
    unlike a compile error (which self-names its file via a file:[line,col]
    locator). The filepath IS known in the caller's loop scope at the exact raise
    point - kriya/workflow/failure.py's Failure/QualityGateFailure now captures it
    there instead of losing it. Confirms the persisted gate_outcomes entry (read
    back from a real traces.db, not a mock) for the anchor-match failure carries
    the real filepath in both likely_files and file_locations."""
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.paths.logs = str(tmp_path / "logs")
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write App.java",
        "Review: Approved",
    ])

    we = WorkflowEngine(kernel, llm)
    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check") as mock_compile:
        mock_compile.side_effect = [
            {"success": False, "output": "[ERROR] .../App.java:[2,3] cannot find symbol"},
            {"success": True, "output": ""},
        ]
        we.developer.run_generation = AsyncMock(
            side_effect=[
                # Attempt 1: full content, fails compile above.
                [{"filepath": "App.java", "content": "class App {\n  Object x;\n}"}],
                # Attempt 2: an anchored edit whose search block does not match the
                # real on-disk content at all - forces apply_anchored_edits() to
                # raise "matched 0 times" (never reaches the compile check).
                [{"filepath": "App.java", "edits": [
                    {"search": "this text does not appear anywhere in App.java", "replace": "class App {}"}
                ]}],
                # Attempt 3: full content again, succeeds compile.
                [{"filepath": "App.java", "content": "class App {\n  String x;\n}"}],
            ]
        )
        res = await we.run_generation_workflow(goal="Create a Java app", workspace_path=str(tmp_path))

    assert res["quality_gates_passed"] is True

    db_path = os.path.join(cfg.paths.logs, "traces.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM runs ORDER BY timestamp DESC LIMIT 1").fetchone()
    conn.close()
    gate_outcomes = json.loads(row["gate_outcomes"])

    anchor_failures = [o for o in gate_outcomes if o.get("type") == "anchored_edit"]
    assert len(anchor_failures) == 1, gate_outcomes
    assert anchor_failures[0]["likely_files"] == ["App.java"]
    assert anchor_failures[0]["file_locations"] == [{"filepath": "App.java", "line": None, "col": None}]
    assert anchor_failures[0]["success"] is False
    # Forensics gap closed 2026-08-07: the real pre-edit file content and the
    # exact search/replace text that failed to match must both actually
    # reach traces.db, not just be captured in memory and dropped before
    # persistence - found live as undiagnosable via a real
    # kriya-protocol-parser-app anchor-match failure.
    assert anchor_failures[0]["failed_content"] == {"App.java": "class App {\n  Object x;\n}"}
    assert anchor_failures[0]["attempted_edits"] == [
        {"search": "this text does not appear anywhere in App.java", "replace": "class App {}"}
    ]


@pytest.mark.asyncio
async def test_workflow_anchored_edit_failure_redirects_to_the_real_target_file(tmp_path):
    """End-to-end regression test for the a-3 ignite_qpid_protocol incident
    (2026-08-14): a compile error names only App.java, so the targeted retry is
    scoped to App.java alone - but the model's edit is actually a fix for
    Helper.java (its search block is lifted verbatim from Helper.java's real
    content), so it matches 0 times against App.java. Confirms this now
    produces a "misdirected_edit" gate_outcome naming BOTH files in
    likely_files (widening the next retry's scope), instead of the generic
    "anchored_edit"/"matched 0 times" failure that used to burn the retry with
    no way to recover."""
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.paths.logs = str(tmp_path / "logs")
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write App.java and Helper.java",
        "Review: Approved",
    ])

    we = WorkflowEngine(kernel, llm)
    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check") as mock_compile:
        mock_compile.side_effect = [
            {"success": False, "output": "[ERROR] .../App.java:[2,3] cannot find symbol"},
            {"success": True, "output": ""},
        ]
        we.developer.run_generation = AsyncMock(
            side_effect=[
                # Attempt 1: full content for both files, App.java fails compile.
                [
                    {"filepath": "App.java", "content": "class App {\n  Object x;\n}"},
                    {"filepath": "Helper.java", "content": "class Helper {\n  static final int Y = 5;\n}"},
                ],
                # Attempt 2: targeted retry scoped to App.java (the only file the
                # compile error names) - but the edit's search block is actually
                # Helper.java's own content, never App.java's.
                [{"filepath": "App.java", "edits": [
                    {"search": "static final int Y = 5;", "replace": "static final int Y = 6;"}
                ]}],
                # Attempt 3: full content for App.java, succeeds compile.
                [{"filepath": "App.java", "content": "class App {\n  String x;\n}"}],
            ]
        )
        res = await we.run_generation_workflow(goal="Create a Java app", workspace_path=str(tmp_path))

    assert res["quality_gates_passed"] is True

    db_path = os.path.join(cfg.paths.logs, "traces.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM runs ORDER BY timestamp DESC LIMIT 1").fetchone()
    conn.close()
    gate_outcomes = json.loads(row["gate_outcomes"])

    misdirected = [o for o in gate_outcomes if o.get("type") == "misdirected_edit"]
    assert len(misdirected) == 1, gate_outcomes
    assert set(misdirected[0]["likely_files"]) == {"App.java", "Helper.java"}
    assert {loc["filepath"] for loc in misdirected[0]["file_locations"]} == {"App.java", "Helper.java"}
    assert "Helper.java" in misdirected[0]["output"]
    # No generic "anchored_edit" outcome should have been recorded for this
    # attempt - the misdirected-file check pre-empts it entirely.
    assert not [o for o in gate_outcomes if o.get("type") == "anchored_edit"]


def test_find_edits_ignoring_reported_line_flags_a_real_non_fix():
    """Regression test for a real live failure, 2026-08-07 (ignite_qpid_person):
    a targeted retry's own SEARCH block spanned the exact line the compiler
    reported, but its REPLACE text at that same relative position was
    byte-identical - a method call got renamed nearby, but the actual
    reported line (`Person person = cache.get(1);`, missing a cast) was never
    touched. The edit applied cleanly (no anchor-match failure - Kriya's own
    plumbing worked fine), so the identical compile error simply recurred on
    the next attempt."""
    orig = (
        "public class PersonApp {\n"
        "    private static void readFromIgniteCacheAndPrint() throws Exception {\n"
        "        try (Ignite ignite = Ignition.start(\"ignite-config.xml\")) {\n"
        "            var cache = ignite.cache(CACHE_NAME);\n"
        "            Person person = cache.get(1);\n"
        "        }\n"
        "    }\n"
        "}\n"
    )
    edit = {
        "search": (
            "            var cache = ignite.cache(CACHE_NAME);\n"
            "            Person person = cache.get(1);"
        ),
        "replace": (
            "            var cache = ignite.getOrCreateCache(CACHE_NAME);\n"
            "            Person person = cache.get(1);"
        ),
    }
    error_context = (
        "PersonApp.java:[5,38] incompatible types: java.lang.Object cannot be converted to com.example.Person"
    )
    ignored = find_edits_ignoring_reported_line(orig, [edit], "PersonApp.java", error_context)
    assert ignored == [5]


def test_find_edits_ignoring_reported_line_does_not_flag_a_valid_alternative_fix():
    # A legitimate fix that corrects the DECLARATION instead of the usage
    # site (explicit generics on `cache`, leaving `cache.get(1)` needing no
    # change at all) must NOT be flagged - the search block here doesn't even
    # span the reported line, so Layer 1 has no basis to second-guess it.
    orig = (
        "public class PersonApp {\n"
        "    var cache = ignite.cache(CACHE_NAME);\n"
        "    Person person = cache.get(1);\n"
        "}\n"
    )
    edit = {
        "search": "    var cache = ignite.cache(CACHE_NAME);",
        "replace": "    IgniteCache<Integer, Person> cache = ignite.getOrCreateCache(CACHE_NAME);",
    }
    error_context = (
        "PersonApp.java:[3,20] incompatible types: java.lang.Object cannot be converted to com.example.Person"
    )
    ignored = find_edits_ignoring_reported_line(orig, [edit], "PersonApp.java", error_context)
    assert ignored == []


def test_find_edits_ignoring_reported_line_empty_without_a_locatable_error():
    ignored = find_edits_ignoring_reported_line(
        "class App {}", [{"search": "class App {}", "replace": "class App {}"}],
        "App.java", "some error with no file:[line,col] locator at all",
    )
    assert ignored == []


# --- find_whole_response_no_op(): Layer 0 - did this response change ANYTHING at all? ---

def test_find_whole_response_no_op_flags_a_single_identical_edit():
    """Reproduces the real b-10l incident (design doc §7.32): the model
    claimed "I will replace the entire file content" but SEARCH and REPLACE
    were byte-identical - a pure no-op needing no prose comparison to
    catch."""
    edits = [{
        "search": '<?xml version="1.0" encoding="UTF-8"?>\n<beans>...</beans>\n',
        "replace": '<?xml version="1.0" encoding="UTF-8"?>\n<beans>...</beans>\n',
    }]
    assert find_whole_response_no_op(edits) is True


def test_find_whole_response_no_op_ignores_whitespace_only_differences():
    # normalize_whitespace-equivalent (differing indentation/blank lines
    # only) is still a no-op - the same tolerance apply_anchored_edits
    # itself already uses for matching.
    edits = [{
        "search": "line one\n  line two",
        "replace": "line one\n    line two\n",
    }]
    assert find_whole_response_no_op(edits) is True


def test_find_whole_response_no_op_does_not_flag_a_real_change():
    edits = [{"search": "int x = 1;", "replace": "int x = 2;"}]
    assert find_whole_response_no_op(edits) is False


def test_find_whole_response_no_op_does_not_flag_a_mixed_response():
    """Reproduces the b-10f companion-edit shape (§7.29): a harmless no-op
    edit sitting alongside a genuinely real one in the SAME response must
    NOT be flagged - only a response where EVERY edit does nothing is a
    whole-response no-op."""
    edits = [
        {"search": "import bad.pkg.Cache;", "replace": "import good.pkg.Cache;"},
        {"search": "  Cache c = get();", "replace": "  Cache c = get();"},
    ]
    assert find_whole_response_no_op(edits) is False


def test_find_whole_response_no_op_empty_or_missing_edits():
    assert find_whole_response_no_op([]) is False
    assert find_whole_response_no_op(None) is False


# --- find_misdirected_edit_target(): did a failed anchor match aim at the wrong file? ---

def test_find_misdirected_edit_target_finds_the_real_target():
    # Reproduces the real a-3 ignite_qpid_protocol shape: the edit was scoped to
    # ProtocolApp.java (whose real content doesn't contain the encode() body at
    # all) but its search block is actually lifted from ProtocolParser.java.
    orig_text = "public class ProtocolApp {\n    private static void testProtocolLayer() {}\n}\n"
    edits = [{
        "search": "int dataLength = protocol.body.length;",
        "replace": "int dataLength = protocol.dataLength;",
    }]
    other_files = {
        "ProtocolParser.java": (
            "public class ProtocolParser {\n"
            "    public static byte[] encode(Protocol protocol) {\n"
            "        int dataLength = protocol.body.length;\n"
            "        return null;\n"
            "    }\n"
            "}\n"
        ),
    }
    result = find_misdirected_edit_target(edits, orig_text, other_files)
    assert result == "ProtocolParser.java"


def test_find_misdirected_edit_target_none_when_no_other_file_matches_either():
    orig_text = "public class ProtocolApp {}\n"
    edits = [{"search": "this text exists nowhere", "replace": "irrelevant"}]
    other_files = {"ProtocolParser.java": "public class ProtocolParser {}\n"}
    assert find_misdirected_edit_target(edits, orig_text, other_files) is None


def test_find_misdirected_edit_target_skips_edits_that_already_matched():
    # An edit whose search block DOES match its own intended file is never the
    # culprit, even if that same text happens to also appear elsewhere (e.g. a
    # shared constant) - only a failing edit's search block is ever checked.
    orig_text = "public class ProtocolApp {\n    static final int X = 9;\n}\n"
    edits = [{"search": "static final int X = 9;", "replace": "static final int X = 10;"}]
    other_files = {"Other.java": "public class Other {\n    static final int X = 9;\n}\n"}
    assert find_misdirected_edit_target(edits, orig_text, other_files) is None


def test_find_misdirected_edit_target_tolerates_whitespace_drift():
    # Leading/trailing whitespace and blank-line differences are tolerated
    # (same tolerance apply_anchored_edits() itself uses) - internal
    # mid-line spacing is not, consistent with normalize_whitespace()'s own
    # per-line-strip-only philosophy used throughout this module.
    orig_text = "public class ProtocolApp {}\n"
    edits = [{"search": "  int dataLength = protocol.body.length;  ", "replace": "x"}]
    other_files = {"ProtocolParser.java": "public class ProtocolParser {\n\n\nint dataLength = protocol.body.length;\n}\n"}
    assert find_misdirected_edit_target(edits, orig_text, other_files) == "ProtocolParser.java"


def test_find_misdirected_edit_target_empty_other_files():
    orig_text = "public class ProtocolApp {}\n"
    edits = [{"search": "does not match anything", "replace": "x"}]
    assert find_misdirected_edit_target(edits, orig_text, {}) is None


# --- find_edits_ignoring_own_diagnosis(): does the edit implement its own analysis? ---

def test_find_edits_ignoring_own_diagnosis_flags_a_fix_that_never_happened():
    # The analysis names a specific fix; the edit changes something else,
    # leaving the actual fix content nowhere in the new code.
    analysis = "the fix requires declaring the cache with explicit generic type parameters `IgniteCache<Integer, Protocol>` instead of using `var`."
    edits = [{"search": "var cache = ignite.getOrCreateCache(\"protocol-cache\");",
              "replace": "var cache = ignite.getOrCreateCache(\"protocol-cache-v2\");"}]
    result = find_edits_ignoring_own_diagnosis(analysis, edits, None, "irrelevant orig text")
    assert result is not None
    assert "IgniteCache<Integer, Protocol>" in result


def test_find_edits_ignoring_own_diagnosis_passes_when_the_fix_is_present():
    analysis = "the fix requires declaring the cache with explicit generic type parameters `IgniteCache<Integer, Protocol>` instead of using `var`."
    edits = [{"search": "var cache = ignite.getOrCreateCache(\"protocol-cache\");",
              "replace": "IgniteCache<Integer, Protocol> cache = ignite.getOrCreateCache(\"protocol-cache\");"}]
    assert find_edits_ignoring_own_diagnosis(analysis, edits, None, "irrelevant orig text") is None


def test_find_edits_ignoring_own_diagnosis_excludes_the_problem_quote_already_in_old_code():
    # `var` (describing the PROBLEM) is already present in the old code -
    # must not count as evidence the fix was made just because it's a quoted
    # span that happens to still be there. This is the exact old-vs-new
    # ambiguity the design resolves.
    analysis = "the fix requires explicit generics `IgniteCache<Integer, Protocol>` instead of `var`."
    edits = [{"search": "var cache = ignite.getOrCreateCache(\"x\");",
              "replace": "var cacheTwo = ignite.getOrCreateCache(\"x\");"}]  # `var` still present, no real fix
    result = find_edits_ignoring_own_diagnosis(analysis, edits, None, "irrelevant")
    assert result is not None


def test_find_edits_ignoring_own_diagnosis_passes_with_no_quoted_spans():
    # A prose-only diagnosis with no backtick-quoted code has nothing
    # specific enough to check against - never flag it.
    analysis = "the null check was missing before dereferencing the object."
    edits = [{"search": "x", "replace": "y"}]
    assert find_edits_ignoring_own_diagnosis(analysis, edits, None, "irrelevant") is None


def test_find_edits_ignoring_own_diagnosis_passes_with_no_analysis():
    assert find_edits_ignoring_own_diagnosis(None, [{"search": "x", "replace": "y"}], None, "irrelevant") is None
    assert find_edits_ignoring_own_diagnosis("", [{"search": "x", "replace": "y"}], None, "irrelevant") is None


def test_find_edits_ignoring_own_diagnosis_handles_full_content_shape():
    analysis = "should use `StringBuilder` instead of string concatenation in a loop."
    orig_text = "String result = \"\";\nfor (String s : parts) { result += s; }\n"
    fixed_content = "StringBuilder result = new StringBuilder();\nfor (String s : parts) { result.append(s); }\n"
    assert find_edits_ignoring_own_diagnosis(analysis, None, fixed_content, orig_text) is None

    unfixed_content = "String result = \"\";\nfor (String s : parts) { result = result + s; }\n"
    result = find_edits_ignoring_own_diagnosis(analysis, None, unfixed_content, orig_text)
    assert result is not None
    assert "StringBuilder" in result


def test_find_edits_ignoring_own_diagnosis_passes_a_correct_wrap_edit():
    """Regression test for a real false-positive bug found live (2026-08-13,
    python_greeter, reproduced identically across two separate eval-harness
    runs, both qwen3-coder:30b AND glm-4.7-flash): this check was flagging a
    genuinely CORRECT fix ("wrap the bare marker in a print() call") as a
    mismatch on every single retry, because the quoted marker text is
    unavoidably identical before and after a correct wrap edit - the old
    signal (quoted span must be NEW, not in old text) can never be satisfied
    by a wrap/extend edit that deliberately preserves the quoted text intact.
    Not a model execution failure at all - this check's own false positive
    was actively blocking an already-correct fix."""
    analysis = (
        "The previous implementation had a bare verification marker `[VERIFICATION] PASS` "
        "on line 6 which was not wrapped in a print() call. The fix involves wrapping this "
        "literal in a proper print() function call."
    )
    edits = [{"search": "[VERIFICATION] PASS", "replace": "print(\"[VERIFICATION] PASS\")"}]
    assert find_edits_ignoring_own_diagnosis(analysis, edits, None, "irrelevant orig text") is None


def test_find_edits_ignoring_own_diagnosis_still_flags_unrelated_change_near_a_wrap_shape():
    # A wrap-shaped edit that DOESN'T actually wrap the diagnosed text (changes
    # something else nearby, leaves the marker exactly as broken as before)
    # must still be flagged - the fix above must not accidentally accept any
    # edit that merely touches the same area.
    analysis = "wrap the bare marker `[VERIFICATION] PASS` in a print() call."
    edits = [{"search": "[VERIFICATION] PASS\nx = 1", "replace": "[VERIFICATION] PASS\nx = 2"}]
    result = find_edits_ignoring_own_diagnosis(analysis, edits, None, "irrelevant")
    assert result is not None


def test_find_edits_ignoring_own_diagnosis_regression_validation_3():
    """Regression test for the real, live-reproduced failure that motivated
    this check (2026-08-13, spikes/eval_harness/runs/attribution-fix-
    validation-3): a compile error recurred verbatim across 3 consecutive
    targeted retries despite a textbook-correct FIX ANALYSIS every time.
    Exact analysis text from kriya.log, attempt 1's retry."""
    analysis = (
        "The error occurs because `ignite.getOrCreateCache(\"protocol-cache\")` returns "
        "a generic `IgniteCache` object without explicit type parameters, causing the "
        "compiler to infer it as `IgniteCache<Object, Object>`. When calling "
        "`cache.get(1)`, the return value is `Object` which cannot be implicitly "
        "converted to `Protocol`. The fix requires either explicitly casting the result "
        "or declaring the cache with proper generic type parameters. Since we're using "
        "`getOrCreateCache` and need to store `Protocol` objects, we should declare the "
        "cache with explicit generic types `IgniteCache<Integer, Protocol>`."
    )
    # Reproduces what the worktree evidence showed actually happened: an edit
    # that leaves the untyped `var cache = ...` declaration unchanged.
    unfixed_edits = [{
        "search": "var cache = ignite.getOrCreateCache(\"protocol-cache\");",
        "replace": "var cache = ignite.getOrCreateCache(\"protocol-cache\"); // unchanged",
    }]
    assert find_edits_ignoring_own_diagnosis(analysis, unfixed_edits, None, "irrelevant") is not None

    fixed_edits = [{
        "search": "var cache = ignite.getOrCreateCache(\"protocol-cache\");",
        "replace": "IgniteCache<Integer, Protocol> cache = ignite.getOrCreateCache(\"protocol-cache\");",
    }]
    assert find_edits_ignoring_own_diagnosis(analysis, fixed_edits, None, "irrelevant") is None


def test_find_edits_ignoring_own_diagnosis_a_second_unrelated_edit_does_not_poison_a_correct_one():
    """Regression test for a real false-positive bug found live, 2026-08-16
    (ignite_qpid_person, runs b-8/b-9): a genuinely correct, single-line cast
    fix ("return cache.get(1);" -> "return (Person) cache.get(1);" -
    textbook signal (a), "Person" is real new content) was flagged as a
    mismatch anyway, because the SAME multi-edit response also included a
    second, unrelated edit elsewhere in the file whose OWN search text
    happened to already mention "Person" (a common identifier in a
    Person-caching app). The old implementation flat-joined every edit's
    search/replace into one shared old_text/new_text pool before checking
    "was this quote already present" - so the unrelated edit's search text
    poisoned the check for a completely different, already-correct edit. A
    response with more than one SEARCH/REPLACE pair in the same retry is
    normal, not a rare shape - confirmed directly via this pure function,
    no live model call needed to reproduce it."""
    analysis = (
        "The compilation error `Type mismatch: cannot convert from Object to Person` occurs "
        "because `ignite.getOrCreateCache(CACHE_NAME)` returns an untyped cache, causing "
        "`cache.get(1)` to return `Object`. I will explicitly cast the result of "
        "`cache.get(1)` to `Person` in the `readFromCache` method to resolve the type mismatch."
    )
    edits = [
        {"search": "return cache.get(1);", "replace": "return (Person) cache.get(1);"},
        # Unrelated no-op edit elsewhere in the same response - its search
        # text just happens to already mention "Person", for reasons that
        # have nothing to do with the fix above.
        {
            "search": "Person cachedPerson = readFromCache(ignite);",
            "replace": "Person cachedPerson = readFromCache(ignite);",
        },
    ]
    assert find_edits_ignoring_own_diagnosis(analysis, edits, None, "return cache.get(1);") is None


def test_find_edits_ignoring_own_diagnosis_still_flags_a_genuine_multi_edit_no_op():
    # Companion negative case for the fix above - a multi-edit response that
    # genuinely never makes the diagnosed change (in ANY of its edits) must
    # still be flagged, not accidentally waved through just because there
    # are multiple edits now.
    analysis = "the fix requires declaring `IgniteCache<Integer, Protocol>` explicitly instead of `var`."
    edits = [
        {"search": "var cache = get();", "replace": "var cache = get(); // still untyped"},
        {"search": "int unrelated = 1;", "replace": "int unrelated = 2;"},
    ]
    assert find_edits_ignoring_own_diagnosis(analysis, edits, None, "irrelevant") is not None


def test_find_edits_ignoring_own_diagnosis_recognizes_a_deletion_shaped_fix():
    """Regression test for a second, distinct false-positive bug found live,
    2026-08-16 (ignite_qpid_person, run b-10a) - different from the
    multi-edit-poisoning bug fixed above. A diagnosis that corrects a WRONG
    qualified identifier to a RIGHT one (a deletion/substitution, not an
    addition) never satisfies the additive signal (a)/(b): every quoted
    fragment of a dotted identifier being shortened - "IgniteCache",
    "org.apache.ignite.cache", "org.apache.ignite" - is trivially a
    substring of BOTH the old and new text, so nothing reads as genuinely
    "new." Real analysis text from the incident, reproduced verbatim -
    correcting `import org.apache.ignite.cache.IgniteCache;` to
    `import org.apache.ignite.IgniteCache;` is a completely correct fix and
    must NOT be flagged as a mismatch."""
    analysis = (
        "The error occurs because the code imports `org.apache.ignite.cache.IgniteCache` on "
        "line 6, but according to the engineering skill conventions for Ignite, `IgniteCache` "
        "is not located in the `org.apache.ignite.cache` package as incorrectly imported. The "
        "correct import location for `IgniteCache` is the top-level `org.apache.ignite` "
        "package. This is a specific, documented fact in the Ignite skill conventions that was "
        "missed in the original code. The fix requires changing the import statement to use "
        "the correct package path."
    )
    edits = [{
        "search": "import org.apache.ignite.cache.IgniteCache;",
        "replace": "import org.apache.ignite.IgniteCache;",
    }]
    assert find_edits_ignoring_own_diagnosis(
        analysis, edits, None, "import org.apache.ignite.cache.IgniteCache;"
    ) is None


def test_find_edits_ignoring_own_diagnosis_deletion_signal_still_flags_a_true_no_op():
    # Companion negative case - the SAME import left completely unchanged
    # must still be flagged, not waved through just because the wrong,
    # fully-qualified quote happens to appear in the (unchanged) search text.
    analysis = (
        "The error occurs because the code imports `org.apache.ignite.cache.IgniteCache` on "
        "line 6. The correct import location is the top-level `org.apache.ignite` package."
    )
    edits = [{
        "search": "import org.apache.ignite.cache.IgniteCache;",
        "replace": "import org.apache.ignite.cache.IgniteCache;",
    }]
    assert find_edits_ignoring_own_diagnosis(
        analysis, edits, None, "import org.apache.ignite.cache.IgniteCache;"
    ) is not None


def test_find_edits_ignoring_own_diagnosis_recognizes_a_cast_insertion_fix():
    """Regression test for a fourth, distinct false-positive bug found live,
    2026-08-16 (ignite_qpid_person, run b-10h), via the new raw-completion
    DEBUG logging (kriya/agents/agent.py). A missing-cast diagnosis quotes
    the operand (`cache.get(1)`) and the type (`Person`) as separate spans,
    never the fused `(Person) cache.get(1)` cast expression as one span - a
    model doesn't phrase it that way. Both separate quotes are trivially
    present in BOTH old and new text (only a parenthesized cast prefix was
    inserted), so none of signals (a)/(b)/(c) see anything "new". Real
    analysis text from the incident, reproduced verbatim - a genuinely
    correct cast-insertion fix was wrongly rejected 3 consecutive retries in
    a row and must NOT be flagged as a mismatch."""
    analysis = (
        "The error occurs because `cache.get(1)` returns an `Object` type, but it's being "
        "assigned directly to a `Person` variable without explicit casting. The compiler "
        "cannot automatically convert from `Object` to `Person`. This is a classic generics "
        "issue where the cache's generic type parameter isn't properly declared, causing the "
        "getter to return raw `Object` instead of the expected `Person` type."
    )
    edits = [{
        "search": "Person cachedPerson = cache.get(1);",
        "replace": "Person cachedPerson = (Person) cache.get(1);",
    }]
    assert find_edits_ignoring_own_diagnosis(
        analysis, edits, None, "Person cachedPerson = cache.get(1);"
    ) is None


def test_find_edits_ignoring_own_diagnosis_cast_signal_still_flags_a_true_no_op():
    # Companion negative case - the SAME cast-missing line left completely
    # unchanged must still be flagged, not waved through just because
    # `Person` happens to appear (unchanged, uncast) in both search and
    # replace.
    analysis = (
        "The error occurs because `cache.get(1)` returns an `Object` type, but it's being "
        "assigned directly to a `Person` variable without explicit casting."
    )
    edits = [{
        "search": "Person cachedPerson = cache.get(1);",
        "replace": "Person cachedPerson = cache.get(1);",
    }]
    assert find_edits_ignoring_own_diagnosis(
        analysis, edits, None, "Person cachedPerson = cache.get(1);"
    ) is not None


# --- find_edits_ignoring_own_diagnosis(): the "removed the old code too" signal ---

def test_find_edits_ignoring_own_diagnosis_flags_a_stale_removal():
    """Regression test for a real gap found 2026-08-14 via
    spikes/protocol_bug_pocs/05_incremental_composition, reproducing a bug
    skills/binary-wire-protocol/rules.txt already documents from live
    incidents: a generated ProtocolParser.java contained BOTH the wrong
    `buffer.putInt(dataLength)` call (writes 4 bytes for a 3-byte wire field)
    AND the correct manual byte-shift replacement, in the SAME method - a
    half-finished migration that throws BufferOverflowException. The old
    addition-only signal would have passed this: the quoted fix genuinely is
    new content. This reproduces the same shape directly against the
    function - a diagnosis naming the old call for removal ("instead of
    `buffer.putInt(dataLength)`"), and an edit that adds the byte-shift fix
    without ever deleting the old call."""
    analysis = (
        "The encode() method incorrectly writes the 3-byte dataLength field using "
        "`buffer.putInt(dataLength)`, which always writes 4 bytes regardless of the "
        "wire format's declared width, overflowing the allocated buffer. Instead of "
        "`buffer.putInt(dataLength)`, manually byte-shift: "
        "`buffer.put((byte) ((dataLength >> 16) & 0xFF))` and so on for each byte."
    )
    edits = [{
        "search": "buffer.putInt(dataLength);",
        "replace": (
            "buffer.putInt(dataLength);\n"
            "buffer.put((byte) ((dataLength >> 16) & 0xFF));\n"
            "buffer.put((byte) ((dataLength >> 8) & 0xFF));\n"
            "buffer.put((byte) (dataLength & 0xFF));"
        ),
    }]
    result = find_edits_ignoring_own_diagnosis(analysis, edits, None, "irrelevant")
    assert result is not None
    assert "buffer.putInt(dataLength)" in result
    assert "still appears" in result


def test_find_edits_ignoring_own_diagnosis_passes_a_clean_removal():
    # Same diagnosis as above, but the edit actually DELETES the old call
    # this time (search block spans both the old call and the surrounding
    # statement, replace contains only the byte-shift fix) - must not flag.
    # The fix itself is also quoted (matching this function's existing
    # addition-check requirement elsewhere in this file - a prose-only
    # description of the fix, with no literal quote for it, can never
    # satisfy that check regardless of the removal signal).
    analysis = (
        "Instead of `buffer.putInt(dataLength)`, manually byte-shift each byte: "
        "`buffer.put((byte) (dataLength & 0xFF))`."
    )
    edits = [{
        "search": "buffer.putInt(dataLength);",
        "replace": (
            "buffer.put((byte) ((dataLength >> 16) & 0xFF));\n"
            "buffer.put((byte) ((dataLength >> 8) & 0xFF));\n"
            "buffer.put((byte) (dataLength & 0xFF));"
        ),
    }]
    assert find_edits_ignoring_own_diagnosis(analysis, edits, None, "irrelevant") is None


def test_find_edits_ignoring_own_diagnosis_removal_signal_ignores_unrelated_substring():
    # The flagged-for-removal span (`var`) is short enough to collide with an
    # unrelated identifier that merely CONTAINS it as a substring
    # (`variance`) - must not false-positive; `var` the keyword itself is
    # genuinely gone. The fix is also quoted, matching this function's
    # existing addition-check requirement.
    analysis = "declare the cache with explicit generics `IgniteCache<Integer, Protocol>` instead of `var`."
    edits = [{
        "search": "var cache = ignite.getOrCreateCache(\"x\");",
        "replace": "IgniteCache<Integer, Protocol> cache = ignite.getOrCreateCache(\"x\"); // variance ok",
    }]
    assert find_edits_ignoring_own_diagnosis(analysis, edits, None, "irrelevant") is None


def test_find_edits_ignoring_own_diagnosis_removal_signal_ignores_result_describing_instead_of():
    """Regression test for a fifth false-positive shape, found live 2026-08-16/17
    (ignite_qpid_person, run b-10i): "instead of" is ambiguous between an
    INSTRUCTION ("do Y instead of `X`" - X is what to discard, the shape
    _REMOVAL_PHRASE_RE was built for) and a RESULT DESCRIPTION ("returns `Object`
    instead of `Person`" - Person is the correct/desired value, expected to still
    appear unchanged in a correct fix). Real analysis text from the incident,
    reproduced verbatim - a genuinely correct cast-insertion fix was wrongly
    rejected by the removal signal (not the addition signals, which already
    correctly recognize this shape - see the cast-insertion test above) twice in
    the same run and must NOT be flagged as a mismatch."""
    analysis = (
        "The compilation error occurs because `cache.get(1)` returns an `Object` type "
        "instead of `Person`, causing a type mismatch when assigned to `Person cachedPerson`. "
        "This happens because the Ignite cache is declared without explicit generic type "
        "parameters, so the compiler infers it as a raw type. The fix requires either adding "
        "an explicit cast or declaring the cache with proper generic type parameters to "
        "ensure type safety and prevent the compilation error."
    )
    edits = [{
        "search": "Person cachedPerson = cache.get(1);",
        "replace": "Person cachedPerson = (Person) cache.get(1);",
    }]
    assert find_edits_ignoring_own_diagnosis(analysis, edits, None, "irrelevant") is None


def test_find_edits_ignoring_own_diagnosis_removal_signal_still_flags_result_describing_shape_when_genuinely_stale():
    # Companion negative case - same "returns X instead of Y" result-describing
    # phrasing must NOT blanket-exempt Y from ever being flagged: if Y (here
    # `Person`) is ALSO explicitly named elsewhere in the analysis as something to
    # remove via a genuine instruction (not just the result-describing clause),
    # and it's genuinely gone from the fix, the removal signal must still have a
    # real, unremoved genuine instruction quote to catch it. This uses two
    # DIFFERENT quoted terms - `Person` (result-describing, exempted) and
    # `legacyCache` (genuine removal instruction) - to confirm the exemption is
    # scoped to the specific term matched by the result-describing pattern, not a
    # blanket pass for the whole analysis.
    analysis = (
        "`cache.get(1)` returns an `Object` type instead of `Person`. Also, stop using "
        "`legacyCache` entirely - it's deprecated."
    )
    edits = [{
        "search": "Person cachedPerson = cache.get(1); legacyCache.close();",
        "replace": "Person cachedPerson = (Person) cache.get(1); legacyCache.close();",
    }]
    result = find_edits_ignoring_own_diagnosis(analysis, edits, None, "irrelevant")
    assert result is not None
    assert "legacyCache" in result


def test_find_edits_ignoring_own_diagnosis_excludes_self_referential_file_path():
    """Regression test for a fifth false-positive shape, found live 2026-08-17
    (ignite_qpid_person, run b-10k), via the raw-completion DEBUG logging. A
    diagnosis for a malformed/missing file routinely backtick-quotes the FILE'S
    OWN PATH purely for self-identification, not as a claimed code fragment - a
    self-referential path obviously never appears literally inside that file's
    own content. Real analysis text and content from the incident, reproduced
    verbatim - genuinely valid, complete XML content was rejected 3 consecutive
    times because the ONLY backtick-quoted span each time was the file's own
    path, and must NOT be flagged as a mismatch."""
    analysis = (
        "The error indicates a malformed XML in `src/main/resources/ignite-config.xml` "
        "with a syntax error at line 1, column 0. This suggests there's either a hidden "
        "character, BOM (Byte Order Mark), or the file is completely empty/invalid. "
        "Looking at the provided example and the task requirements, the ignite-config.xml "
        "file should be a proper Spring XML configuration file defining an "
        "IgniteConfiguration bean."
    )
    content = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
        "<beans xmlns=\"http://www.springframework.org/schema/beans\">\n"
        "    <bean id=\"ignite.cfg\" class=\"org.apache.ignite.configuration.IgniteConfiguration\">\n"
        "        <property name=\"igniteInstanceName\" value=\"ignite-server-node\"/>\n"
        "    </bean>\n"
        "</beans>"
    )
    assert find_edits_ignoring_own_diagnosis(analysis, None, content, "irrelevant") is None


def test_find_edits_ignoring_own_diagnosis_file_path_exclusion_still_flags_a_true_no_op():
    # Companion negative case - a self-referential path quote must not blanket-
    # exempt an analysis that ALSO quotes real code: the code quote must still
    # be checked normally, and a true no-op edit must still be flagged.
    analysis = (
        "The error is in `src/main/resources/ignite-config.xml` - the bean `ignite.cfg` "
        "is missing the required `igniteInstanceName` property."
    )
    content = "<beans><bean id=\"ignite.cfg\"/></beans>"
    assert find_edits_ignoring_own_diagnosis(
        analysis, None, content, "<beans><bean id=\"ignite.cfg\"/></beans>"
    ) is not None


def test_find_edits_ignoring_own_diagnosis_recognizes_a_bare_line_print_wrap_within_a_larger_block():
    """Regression test for a fifth, distinct false-positive bug found live,
    2026-08-17 (ignite_qpid_person, run b-10m). Signal (b) (the existing wrap
    detector) requires the quoted span to equal the ENTIRE search block
    (q == search.strip()) - true for a small, isolated wrap edit, but not
    when the search block also needs surrounding context (the enclosing
    if/else) to match uniquely elsewhere in the file. Real analysis text and
    edit from the incident, reproduced verbatim - Kriya's own
    BareVerificationMarkerCheck correctly flagged a bare, unwrapped
    `[VERIFICATION] PASS` line as invalid Java, the model's repair correctly
    wrapped it in System.out.println(...), and this genuinely correct fix
    was wrongly rejected 3 times across 2 consecutive runs, directly causing
    a 2400s timeout."""
    analysis = (
        "The file contains a stray `[VERIFICATION] PASS` text literal injected directly "
        "into the Java source code on line 69, which breaks the Java syntax parser and "
        "causes the compiler to fail with \"Expression expected after this token\". I will "
        "remove the invalid text and replace it with proper "
        "`System.out.println(\"[VERIFICATION] ...\")` calls inside the conditional branches "
        "to satisfy the verification contract."
    )
    edits = [{
        "search": (
            "                        // Read from cache and print fields\n"
            "                        Person cachedPerson = (Person) cache.get(1);\n"
            "\n"
            "[VERIFICATION] PASS\n"
            "                        if (cachedPerson != null) {\n"
            "                            System.out.println(\"Retrieved Person from cache: \" + cachedPerson);\n"
            "                        } else {\n"
            "                            System.out.println(\"No Person found in cache\");\n"
            "                        }"
        ),
        "replace": (
            "                        // Read from cache and print fields\n"
            "                        Person cachedPerson = (Person) cache.get(1);\n"
            "\n"
            "                        if (cachedPerson != null) {\n"
            "                            System.out.println(\"Retrieved Person from cache: \" + cachedPerson);\n"
            "                            System.out.println(\"[VERIFICATION] PASS\");\n"
            "                        } else {\n"
            "                            System.out.println(\"No Person found in cache\");\n"
            "                            System.out.println(\"[VERIFICATION] FAIL: No Person found in cache\");\n"
            "                        }"
        ),
    }]
    assert find_edits_ignoring_own_diagnosis(analysis, edits, None, "irrelevant") is None


def test_find_edits_ignoring_own_diagnosis_bare_line_wrap_signal_still_flags_unrelated_nearby_change():
    # Same shape as the existing wrap-shape negative test, but multi-line:
    # the marker stays bare and UNCHANGED on its own line while an unrelated
    # nearby line changes - must still be flagged, not waved through just
    # because the marker happens to sit in a multi-line search block.
    analysis = "wrap the bare marker `[VERIFICATION] PASS` in a print() call."
    edits = [{"search": "[VERIFICATION] PASS\nx = 1", "replace": "[VERIFICATION] PASS\nx = 2"}]
    assert find_edits_ignoring_own_diagnosis(analysis, edits, None, "irrelevant") is not None


def test_find_edits_ignoring_own_diagnosis_bare_line_wrap_signal_still_flags_trailing_comment_without_print():
    # Companion negative case for the new signal specifically: a bare marker
    # line gaining an unrelated trailing comment (no print/println/puts call
    # anywhere) must still be flagged - this is the exact regression shape
    # signal (b)'s own narrow `q == search.strip()` scoping was built to
    # avoid, now re-verified against the new, broader-context signal too.
    analysis = "The problem is that `[VERIFICATION] PASS` is never logged."
    edits = [{"search": "x = 1\n[VERIFICATION] PASS", "replace": "x = 1\n[VERIFICATION] PASS // TODO"}]
    assert find_edits_ignoring_own_diagnosis(analysis, edits, None, "irrelevant") is not None


def test_find_edits_ignoring_own_diagnosis_removal_signal_takes_priority_over_addition():
    # The addition-check alone would pass this (the fix content genuinely is
    # new), but the removal-check must still catch it - the two signals are
    # independent, and either one failing is a real mismatch.
    analysis = "Use `dataLength` computed fresh from body.length, instead of `protocol.dataLength`."
    edits = [{
        "search": "int dataLength = protocol.dataLength;",
        "replace": "int dataLength = protocol.body.length; int old = protocol.dataLength;",
    }]
    result = find_edits_ignoring_own_diagnosis(analysis, edits, None, "irrelevant")
    assert result is not None
    assert "protocol.dataLength" in result


def test_find_edits_ignoring_own_diagnosis_removal_signal_handles_full_content_shape():
    # The fix is quoted as the literal call actually used (`sb.append(s)`),
    # matching this function's existing addition-check requirement - a
    # prose-only description of the fix can never satisfy it.
    analysis = "Instead of `result += s`, use `sb.append(s)`."
    orig_text = "String result = \"\";\nfor (String s : parts) { result += s; }\n"
    # Half-finished: appends a StringBuilder line but never deletes the old one.
    unfixed_content = (
        "StringBuilder sb = new StringBuilder();\n"
        "String result = \"\";\n"
        "for (String s : parts) { result += s; sb.append(s); }\n"
    )
    result = find_edits_ignoring_own_diagnosis(analysis, None, unfixed_content, orig_text)
    assert result is not None
    assert "result += s" in result

    fixed_content = "StringBuilder sb = new StringBuilder();\nfor (String s : parts) { sb.append(s); }\n"
    assert find_edits_ignoring_own_diagnosis(analysis, None, fixed_content, orig_text) is None


@pytest.mark.asyncio
async def test_workflow_unaddressed_error_location_defers_to_the_real_compiler(tmp_path):
    """End-to-end regression test, UPDATED 2026-08-17 for the deterministic-
    validation-first bypass (see _diagnosis_mismatch_bypass_reason's docstring
    addendum and the inline comment at this check's call site in attempt.py):
    Layer 1 (find_edits_ignoring_reported_line) no longer hard-rejects an edit
    whose search block spans a previously-reported compile-error line but
    leaves that exact line unchanged - it's a bypassed, logged-only signal
    now, since a real compile gate always runs right after anyway and is the
    authoritative answer (confirmed live: this exact check false-positived on
    a companion edit elsewhere in the same response that already transitively
    fixed the reported line, ignite_qpid_person run b-10f). So attempt 2's
    no-op edit here now genuinely reaches the compiler, which correctly fails
    it again (the real, unfixed type error) - one extra compile cycle instead
    of a pre-flight rejection, same tradeoff already accepted for
    diagnosis_mismatch's compile-triggered bypass. Confirmed via 3 compile
    calls (attempt 1's real failure, attempt 2's real failure since the edit
    genuinely never changed line 2, and attempt 3's real fix) and zero
    unaddressed_error_location gate outcomes."""
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.paths.logs = str(tmp_path / "logs")
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write App.java",
        "Review: Approved",
    ])

    we = WorkflowEngine(kernel, llm)
    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check") as mock_compile:
        mock_compile.side_effect = [
            {
                "success": False,
                "output": "[ERROR] .../App.java:[2,20] incompatible types: java.lang.Object cannot be converted to java.lang.String",
            },
            {
                "success": False,
                "output": "[ERROR] .../App.java:[2,20] incompatible types: java.lang.Object cannot be converted to java.lang.String",
            },
            {"success": True, "output": ""},
        ]
        we.developer.run_generation = AsyncMock(
            side_effect=[
                # Attempt 1: full content, fails compile above at line 2.
                [{"filepath": "App.java", "content": "class App {\n  String x = get();\n}"}],
                # Attempt 2: search spans line 2 but replace leaves it byte-identical -
                # the exact non-fix pattern found live (something else renamed instead).
                # Now reaches the real compiler and fails there instead of pre-flight.
                [{"filepath": "App.java", "edits": [{
                    "search": "class App {\n  String x = get();\n}",
                    "replace": "class App { // renamed for clarity\n  String x = get();\n}",
                }]}],
                # Attempt 3: a real fix, actually changes line 2 itself.
                [{"filepath": "App.java", "content": "class App {\n  String x = (String) get();\n}"}],
            ]
        )
        res = await we.run_generation_workflow(goal="Create a Java app", workspace_path=str(tmp_path))

    assert res["quality_gates_passed"] is True
    assert mock_compile.call_count == 3  # attempt 2's edit now genuinely reaches the compiler

    db_path = os.path.join(cfg.paths.logs, "traces.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM runs ORDER BY timestamp DESC LIMIT 1").fetchone()
    conn.close()
    gate_outcomes = json.loads(row["gate_outcomes"])

    assert not any(o.get("type") == "unaddressed_error_location" for o in gate_outcomes), gate_outcomes
    failed_compile_outcomes = [
        o for o in gate_outcomes if o.get("type") == "compile" and o.get("success") is False
    ]
    assert len(failed_compile_outcomes) == 2


@pytest.mark.asyncio
async def test_workflow_unaddressed_error_location_bypass_lets_a_companion_edit_through(tmp_path):
    """Regression test for the real live false positive that motivated the
    bypass above (ignite_qpid_person, run b-10f, replayed via the corpus
    survey's DEBUG-capture, 2026-08-17): a targeted retry's FIRST edit fixes
    the actual root cause (a bad import), which transitively resolves every
    usage site the compiler flagged - but the SAME response's second edit is
    a harmless no-op that just echoes one of those now-fixed usage sites back
    unchanged. Layer 1 used to see only the second edit in isolation and hard-
    reject it as "line left unchanged", even though the file it actually
    produces compiles fine. Confirms the fix: this now reaches the real
    compiler, which is the only thing that ever needed to make this call, and
    passes on the first try - no wasted extra compile cycle, unlike the
    genuine-non-fix case above."""
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.paths.logs = str(tmp_path / "logs")
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write App.java",
        "Review: Approved",
    ])

    we = WorkflowEngine(kernel, llm)
    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check") as mock_compile:
        mock_compile.side_effect = [
            {
                "success": False,
                "output": "[ERROR] .../App.java:[3,5] cannot find symbol: class Cache",
            },
            {"success": True, "output": ""},
        ]
        we.developer.run_generation = AsyncMock(
            side_effect=[
                # Attempt 1: full content, fails compile - bad import for Cache.
                [{
                    "filepath": "App.java",
                    "content": "import bad.pkg.Cache;\nclass App {\n  Cache c = get();\n}",
                }],
                # Attempt 2: edit #1 fixes the ACTUAL root cause (the import) -
                # edit #2 is a harmless no-op echo of the now-transitively-fixed
                # usage site the compiler also named at line 2.
                [{"filepath": "App.java", "edits": [
                    {"search": "import bad.pkg.Cache;", "replace": "import good.pkg.Cache;"},
                    {"search": "  Cache c = get();", "replace": "  Cache c = get();"},
                ]}],
            ]
        )
        res = await we.run_generation_workflow(goal="Create a Java app", workspace_path=str(tmp_path))

    assert res["quality_gates_passed"] is True
    assert mock_compile.call_count == 2  # no wasted third compile - the fix genuinely worked first try

    db_path = os.path.join(cfg.paths.logs, "traces.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM runs ORDER BY timestamp DESC LIMIT 1").fetchone()
    conn.close()
    gate_outcomes = json.loads(row["gate_outcomes"])
    assert not any(o.get("type") == "unaddressed_error_location" for o in gate_outcomes), gate_outcomes


def test_estimate_tokens_no_longer_undercounts_punctuation_heavy_identifiers():
    """Regression test for a finding from the 2026-08-12 SME review: the old
    whitespace-split word heuristic (~1.3 tokens/word) treated a long dotted
    Java import as roughly 2 "words" regardless of how many real tokens a
    BPE tokenizer would actually split it into, systematically undercounting
    the exact class of text that feeds this budget allocator."""
    java_import = "import com.example.very.long.package.path.SomeReallyLongClassName;"
    old_word_based_estimate = int(len(java_import.split()) * 1.3)  # what it used to return
    assert estimate_tokens(java_import) > old_word_based_estimate * 2


def test_estimate_tokens_stays_close_to_the_old_estimate_for_ordinary_prose():
    # The new character-based heuristic shouldn't wildly change behavior for
    # normal prose (skills text, error messages) - only fix the code/
    # punctuation-heavy undercount.
    prose = "The quick brown fox jumps over the lazy dog near the riverbank today"
    old_estimate = int(len(prose.split()) * 1.3)
    new_estimate = estimate_tokens(prose)
    assert abs(new_estimate - old_estimate) <= max(3, old_estimate * 0.25)


def test_estimate_tokens_empty_string_is_zero():
    assert estimate_tokens("") == 0


def test_skeletonize_braced_code_ignores_braces_inside_string_literals():
    """Regression test for a finding from the 2026-08-12 SME review: neither
    analyzer.py's Java chunker (see test_analyzer.py's sibling regression
    test) nor this function were comment/string-literal-aware when brace-
    counting - a '{' or '}' inside a Java string literal or line comment
    miscounted and could truncate or merge skeleton boundaries, the exact
    bug class edit_safety.py's _strip_java_comments_and_strings() was
    specifically built to avoid."""
    from kriya.workflow.workflow import skeletonize_braced_code

    content = (
        "public class Foo {\n"
        '    public void bar() { String s = "unexpected } brace"; int x = 1; }\n'
        "    public void baz() { int y = 2; }\n"
        "}\n"
    )
    skeleton = skeletonize_braced_code(content, "skeleton")
    # Both method bodies collapsed cleanly - the embedded '}' inside bar()'s
    # string must not have been mistaken for its real closing brace. Pre-fix,
    # that premature stop left a garbled leftover fragment of bar()'s own
    # body (everything after the embedded '}') spliced in front of baz()'s
    # signature instead of being fully collapsed away.
    assert skeleton == (
        "public class Foo {\n"
        "    public void bar()  { ... }\n"
        "    public void baz()  { ... }\n"
        "}\n"
    )


def test_python_signatures_keep_all_definitions_and_drop_bodies():
    from kriya.workflow.workflow import skeletonize_python

    content = '''import os
from typing import List

def top_level_fn(x, y):
    z = x + y
    return z

class Foo:
    """A class docstring that is not part of its interface."""
    attr = 1

    def __init__(self, x):
        self.x = x

    async def method_a(
        self,
        values: List[int],
    ) -> int:
        result = sum(values)

        def nested(value: int = 1) -> int:
            return value

        return result
'''

    signatures = skeletonize_python(content, "signatures")

    assert signatures == '''import os
from typing import List

def top_level_fn(x, y):
    ...

class Foo:

    def __init__(self, x):
        ...

    async def method_a(
        self,
        values: List[int],
    ) -> int:
        ...

        def nested(value: int = 1) -> int:
            ...
'''.rstrip("\n")
    assert "z = x + y" not in signatures
    assert "self.x = x" not in signatures
    assert "result = sum(values)" not in signatures
    assert "class docstring" not in signatures
    assert "attr = 1" not in signatures


def test_python_signatures_preserve_decorators_and_multiline_class_headers():
    from kriya.workflow.workflow import skeletonize_python

    content = '''@register("handler")
class Handler(
    BaseHandler,
    Protocol,
):
    @property
    @cached(
        ttl=30,
    )
    def value(self) -> str:
        return self._value
'''

    signatures = skeletonize_python(content, "signatures")

    assert signatures == '''@register("handler")
class Handler(
    BaseHandler,
    Protocol,
):
    @property
    @cached(
        ttl=30,
    )
    def value(self) -> str:
        ...'''
    assert "return self._value" not in signatures


def test_braced_signatures_keep_types_constructors_and_multiline_methods_only():
    from kriya.workflow.workflow import skeletonize_braced_code

    content = '''package com.example;
import java.util.List;

public class Bar {
    private String name;

    public Bar(String name) {
        this.name = name;
    }

    public String getName() {
        return name;
    }

    public void update(
        String name,
        List<Integer> values
    ) {
        this.name = name;
        values.clear();
    }
}
'''

    signatures = skeletonize_braced_code(content, "signatures")

    assert signatures == '''package com.example;
import java.util.List;
public class Bar {
    public Bar(String name) { ... }
    public String getName() { ... }
    public void update(
        String name,
        List<Integer> values
    ) { ... }
}'''
    assert "private String name" not in signatures
    assert "this.name = name" not in signatures
    assert "values.clear" not in signatures


def test_braced_signatures_do_not_misattribute_anonymous_scope_methods():
    from kriya.workflow.workflow import skeletonize_braced_code

    content = '''public class Outer {
    private Runnable action = new Runnable() {
        @Override
        public void run() { secret(); }
    };

    static {
        Runnable startup = new Runnable() {
            @Override public void initialize() { secretStartup(); }
        };
    }

    public void execute() { action.run(); }
}

enum Mode {
    ACTIVE {
        @Override public void apply() { secretMode(); }
    };

    public abstract void apply();
}
'''

    signatures = skeletonize_braced_code(content, "signatures")

    assert signatures == '''public class Outer {
    public void execute() { ... }
}
enum Mode {
    public abstract void apply();
}'''
    assert "public void run()" not in signatures
    assert "public void initialize()" not in signatures
    assert "public void apply()" not in signatures
    assert "secret" not in signatures


def test_braced_signatures_keep_annotations_and_semicolon_members():
    from kriya.workflow.context_budget import _braced_constructor_declaration_pattern
    from kriya.workflow.workflow import skeletonize_braced_code

    content = '''@Contract
public interface Service {
    @Deprecated
    void start();

    @Named(
        value = "primary"
    )
    String name();
}

abstract class BaseService {
    @Deprecated
    public abstract void stop() throws java.io.IOException;
}
'''

    signatures = skeletonize_braced_code(content, "signatures")

    assert signatures == '''@Contract
public interface Service {
    @Deprecated
    void start();
    @Named(
        value = "primary"
    )
    String name();
}
abstract class BaseService {
    @Deprecated
    public abstract void stop() throws java.io.IOException;
}'''
    # Constructor patterns are cached by exact enclosing type rather than
    # recompiled at every opening brace inside the same type.
    assert (
        _braced_constructor_declaration_pattern("BaseService")
        is _braced_constructor_declaration_pattern("BaseService")
    )


def test_braced_skeleton_elides_constructors_regardless_of_member_position():
    from kriya.workflow.workflow import skeletonize_braced_code

    constructor_first = '''public class First {
    public First() { this.name = "default"; }
    public First(String name) { this.name = name; }
    public String getName() { return name; }
}
'''
    constructor_middle = '''public class Middle {
    public String getName() { return name; }
    public Middle(String name) { this.name = name; }
    public void reset() { this.name = null; }
}
'''
    constructor_last = '''public class Last {
    public String getName() { return name; }
    public Last(String name) { this.name = name; }
}
'''

    for content, expected_constructors in (
        (constructor_first, ("public First()", "public First(String name)")),
        (constructor_middle, ("public Middle(String name)",)),
        (constructor_last, ("public Last(String name)",)),
    ):
        skeleton = skeletonize_braced_code(content, "skeleton")
        for constructor in expected_constructors:
            assert constructor + "  { ... }" in skeleton
        assert "this.name =" not in skeleton


def test_braced_skeleton_does_not_treat_control_flow_as_members():
    from kriya.workflow.workflow import skeletonize_braced_code

    content = '''public class Flow {
    public void execute() {
        if (ready) {
            for (Item item : items) {
                use(item);
            }
        }
        try {
            run();
        } catch (RuntimeException error) {
            recover(error);
        }
    }
}
'''

    skeleton = skeletonize_braced_code(content, "skeleton")

    assert skeleton == '''public class Flow {
    public void execute()  { ... }
}
'''
    assert skeleton.count("{ ... }") == 1


def test_java_method_signature_core_pattern_is_shared_across_all_three_call_sites():
    """Regression test for a finding from the 2026-08-12 SME review: the
    same Java method-signature-matching regex was independently duplicated
    three times (analyzer.py, graph.py, context_budget.py) - a fix to the
    shared core (e.g. handling a generic return type) previously wouldn't
    propagate to the other two. Confirms all three now compose their own
    trailing-anchor variant on top of ONE shared JAVA_METHOD_SIGNATURE_CORE
    constant, not just that each independently happens to still work."""
    import re

    from kriya.analyzer.analyzer import JAVA_METHOD_SIGNATURE_CORE
    from kriya.analyzer.graph import JAVA_METHOD_SIGNATURE_CORE as graph_core
    from kriya.workflow.context_budget import JAVA_METHOD_SIGNATURE_CORE as context_budget_core

    assert graph_core is JAVA_METHOD_SIGNATURE_CORE
    assert context_budget_core is JAVA_METHOD_SIGNATURE_CORE

    signature = "public List<String> process(int id, String name)"
    assert re.search(JAVA_METHOD_SIGNATURE_CORE + r"\s*\{", signature + " {").group(1) == "process"
    assert re.match(JAVA_METHOD_SIGNATURE_CORE + r"\s*\{?", signature).group(1) == "process"
    assert re.search(JAVA_METHOD_SIGNATURE_CORE + r"\s*$", signature)


def test_reserve_graph_context_budget_subtracts_unbounded_text_size():
    """Regression test for a real live failure, 2026-08-07 (ignite_qpid_person):
    every retry path computed build_code_context()'s budget as a flat fraction
    of the ACTIVE model's context_window, with zero accounting for
    skills_prompt/learned_rag_context - both unbounded strings prepended to
    the SAME prompt afterward. A real run's 5 active skills measured ~6800
    tokens of rules/instructions alone - comfortably absorbed by a large
    primary model's window, but over half of a 16K-context fallback model's
    entire 0.75 budget (12288 tokens) before any code context was even added,
    causing real 400 'prompt is longer than context length' errors."""
    skills_text = "word " * 5000  # ~6500 estimated tokens
    budget = _reserve_graph_context_budget(16384, skills_text, "")
    expected = int(16384 * 0.75) - estimate_tokens(skills_text)
    assert budget == expected
    assert budget < int(16384 * 0.75)  # strictly less than the old flat budget


def test_reserve_graph_context_budget_accounts_for_multiple_unbounded_texts():
    skills_text = "word " * 1000
    learned_rag_text = "word " * 500
    budget = _reserve_graph_context_budget(16384, skills_text, learned_rag_text)
    expected = int(16384 * 0.75) - estimate_tokens(skills_text) - estimate_tokens(learned_rag_text)
    assert budget == expected


def test_reserve_graph_context_budget_ignores_falsy_texts():
    # None/"" entries must not crash or contribute - the common case (no
    # learned_rag_context this run) shouldn't require callers to filter first.
    budget = _reserve_graph_context_budget(16384, "some skills text", "", None)
    assert budget == int(16384 * 0.75) - estimate_tokens("some skills text")


def test_reserve_graph_context_budget_floors_instead_of_going_negative():
    # A pathologically large skills_prompt must still leave build_code_context()
    # something to work with, not collapse to 0 or a negative budget.
    huge_text = "word " * 50000
    assert _reserve_graph_context_budget(16384, huge_text, "") == 1000


def test_reserve_graph_context_budget_barely_affects_a_large_primary_window():
    # A primary model with a much larger context window should still have
    # plenty of budget left after the same skills_prompt overhead - this
    # fix should not meaningfully change behavior for the common, unaffected
    # case, only the fallback-model-with-a-small-window case it targets.
    skills_text = "word " * 5000
    budget = _reserve_graph_context_budget(32768, skills_text, "")
    assert budget > int(32768 * 0.75) * 0.5


def test_reserve_sibling_content_budget_scales_with_context_window():
    # 2026-08-15 external review, Finding 8: sibling-content budget (unlike
    # _reserve_graph_context_budget's 0.75) uses a smaller 0.15 fraction, since
    # sibling content is reference-only material, not the primary content a
    # per-file completion is generating.
    assert _reserve_sibling_content_budget(32768) == int(32768 * 0.15)
    assert _reserve_sibling_content_budget(16384) == int(16384 * 0.15)


def test_reserve_sibling_content_budget_scales_down_for_a_smaller_fallback_model():
    # A fallback model with a smaller context window gets a proportionally
    # smaller sibling budget than the primary model - not one flat number
    # generous for one and starving for the other.
    assert _reserve_sibling_content_budget(16384) < _reserve_sibling_content_budget(32768)


def test_reserve_sibling_content_budget_floors_instead_of_collapsing_to_near_zero():
    # A pathologically small context window must still leave room for at
    # least one typically-sized sibling file's real content.
    assert _reserve_sibling_content_budget(1000) == 500


def _write_deterministic_text_file(path, lines=40, words_per_line=8):
    # Plain .txt content deliberately, not .java/.py: skeletonize_code()'s
    # fallback branch makes "skeleton" tier a no-op (identical to "full")
    # and "signatures" tier a deterministic first-15-lines truncation - the
    # only extension where a test can predict exact token counts without
    # depending on the Java/Python skeletonizers' own internal heuristics.
    line = ("word " * words_per_line).strip() + "\n"
    path.write_text(line * lines)


# full: 40 lines * 8 words = 320 words -> int(320*1.3) = 416 tokens.
# signatures: first 15 lines * 8 words = 120 words (+ a few for the elided
# suffix) -> ~161 tokens. Budget chosen with wide margin above one-full-one-
# signatures (~577) and below two-full (832), so token-estimate rounding
# doesn't make these tests flaky.
_TEXT_FIXTURE_BUDGET = 700


def test_build_code_context_without_scores_keeps_old_categorical_behavior(tmp_path):
    # file_scores omitted (None) - must behave exactly like before this
    # feature existed: every related file degrades before any matched file.
    _write_deterministic_text_file(tmp_path / "Matched.txt")
    _write_deterministic_text_file(tmp_path / "Related.txt")

    ctx = build_code_context(["Matched.txt"], ["Related.txt"], str(tmp_path), budget_limit=_TEXT_FIXTURE_BUDGET)

    assert "File: Related.txt (Tier: signatures)" in ctx
    assert "File: Matched.txt (Tier: full)" in ctx


def test_build_code_context_degrades_lowest_scored_file_first_regardless_of_category(tmp_path):
    """Architectural add-on from a 2026-08-12 SME review (re-ranking
    retrieval): a low-relevance MATCHED file should lose detail before a
    high-relevance RELATED file does - the old categorical rule (all
    related degrade before any matched) can never express this."""
    _write_deterministic_text_file(tmp_path / "WeakMatch.txt")
    _write_deterministic_text_file(tmp_path / "StrongRelated.txt")

    file_scores = {"WeakMatch.txt": 0.1, "StrongRelated.txt": 0.9}
    ctx = build_code_context(
        ["WeakMatch.txt"], ["StrongRelated.txt"], str(tmp_path), budget_limit=_TEXT_FIXTURE_BUDGET,
        file_scores=file_scores,
    )

    assert "File: WeakMatch.txt (Tier: signatures)" in ctx
    assert "File: StrongRelated.txt (Tier: full)" in ctx


def test_build_code_context_treats_a_file_missing_from_scores_as_lowest_priority(tmp_path):
    _write_deterministic_text_file(tmp_path / "Scored.txt")
    _write_deterministic_text_file(tmp_path / "Unscored.txt")

    ctx = build_code_context(
        ["Scored.txt"], ["Unscored.txt"], str(tmp_path), budget_limit=_TEXT_FIXTURE_BUDGET,
        file_scores={"Scored.txt": 0.5},  # "Unscored.txt" absent
    )

    assert "File: Unscored.txt (Tier: signatures)" in ctx
    assert "File: Scored.txt (Tier: full)" in ctx


@pytest.mark.asyncio
async def test_workflow_wires_hybrid_match_scores_into_graph_rag_context_degradation(tmp_path):
    """Architectural add-on from a 2026-08-12 SME review (re-ranking
    retrieval): confirms the real wiring inside run_generation_workflow's
    Graph RAG retrieval block - not just the pure build_code_context()/
    get_neighborhood() units in isolation - actually threads a matched
    file's real hybrid-search score through to context assembly, so a
    weakly-matched file loses detail in the prompt before a strongly-
    matched one does."""
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.paths.memory = str(tmp_path / "memory")
    # Isolate from the real repo's shared skills/ directory - SkillEngine
    # always loads Kriya's own global skill library too, which would inflate
    # convention_prompt unpredictably and throw off the tuned budget below.
    cfg.paths.skills = str(tmp_path / "skills")
    # NOTE: _reserve_graph_context_budget() floors its return value at
    # _MIN_GRAPH_CONTEXT_BUDGET (1000 tokens) regardless of context_window,
    # so the effective budget here is exactly 1000, not 0.75*context_window -
    # fixture sizes below (64 lines/8 words -> ~655 tokens full, ~161
    # signatures) are chosen against THAT floor: two-full (~1310) exceeds it,
    # one-full-one-signatures (~816) comfortably doesn't.
    cfg.llm.context_window = 1000
    os.makedirs(cfg.paths.memory, exist_ok=True)

    _write_deterministic_text_file(tmp_path / "HighRel.txt", lines=64)
    _write_deterministic_text_file(tmp_path / "LowRel.txt", lines=64)

    from kriya.memory.vector import LocalVectorStore
    dim = 768
    high_emb = [1.0] + [0.0] * (dim - 1)
    low_emb = [0.6, 0.8] + [0.0] * (dim - 2)
    vs = LocalVectorStore(os.path.join(cfg.paths.memory, "vector_index.db"))
    vs.add_document("HighRel.txt", "chunk one", high_emb, chunk_index=0, model_name=cfg.embedding.model, dimensions=dim)
    vs.add_document("LowRel.txt", "chunk two", low_emb, chunk_index=0, model_name=cfg.embedding.model, dimensions=dim)
    vs.close()

    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write math.py",
        "def add(a,b):\n    return a+b",
        "Review: Approved"
    ])
    we = WorkflowEngine(kernel, llm)

    query_emb = [1.0] + [0.0] * (dim - 1)
    with patch("kriya.memory.vector.OllamaEmbeddingClient.get_embedding", new=AsyncMock(return_value=query_emb)):
        res = await we.run_generation_workflow(
            goal="Create math library",
            workspace_path=str(tmp_path)
        )

    assert res["quality_gates_passed"] is True

    # The Planner's own prompt is the first one built with convention_prompt
    # (which now carries the score-degraded Graph RAG context) already
    # folded in.
    planner_prompt = llm.complete.call_args_list[0].args[1]
    assert "File: HighRel.txt (Tier: full)" in planner_prompt
    assert "File: LowRel.txt (Tier: signatures)" in planner_prompt


def test_find_structural_corruption_catches_the_real_duplicate_class_shape():
    """Regression test for a real live corruption, 2026-08-08
    (ignite_qpid_protocol, run 20260808-053604): a model's SEARCH/REPLACE
    edit folded an entire redundant, unasked-for full-file dump (its own
    "package"/"public class" declaration included) into the replace text,
    because the old truncation regex only recognized the literal
    "FILE CONTENT:" marker and this response phrased it as "Corrected file
    content for '...':" instead. Applying that edit produced a file with two
    package statements and two class declarations - a real 23-error
    "illegal start of expression"/"class expected" javac cascade.

    Note a *complete*, self-closed duplicate class is brace-count-balanced
    by construction (each fragment sums to zero net braces regardless of
    ordering), so this best-effort heuristic can't catch that variant on its
    own - that's what the regex fix in kriya/agents/agent.py is for. What
    this safety net does catch is the equally-real variant below, where the
    redundant dump gets cut off mid-generation (token budget) before its own
    duplicate class closes."""
    orig = (
        "package com.example;\n\n"
        "public class ProtocolParser {\n"
        "    public static byte[] encode(Protocol protocol) {\n"
        "        buffer.putInt(protocol.getDataLength());\n"
        "        return buffer.array();\n"
        "    }\n"
        "}\n"
    )
    edit = {
        "search": "        buffer.putInt(protocol.getDataLength());",
        "replace": (
            "        buffer.putInt(protocol.getDataLength()); // corrected\n"
            "\n"
            "Corrected file content for 'ProtocolParser.java':\n"
            "```java\n"
            "package com.example;\n"
            "\n"
            "public class ProtocolParser {\n"
            "    public static byte[] encode(Protocol protocol) {\n"
        ),
    }
    from kriya.workflow.workflow import apply_anchored_edits
    corrupted = apply_anchored_edits(orig, [edit], orig)
    problem = find_structural_corruption("ProtocolParser.java", corrupted)
    assert problem is not None
    assert "brace" in problem


def test_find_structural_corruption_none_for_balanced_java():
    valid = (
        "package com.example;\n\n"
        "public class Foo {\n"
        "    void bar() {\n"
        "        if (true) { System.out.println(\"{ not a real brace }\"); }\n"
        "        // a comment with a { stray brace }\n"
        "        /* a block comment with { another } */\n"
        "    }\n"
        "}\n"
    )
    assert find_structural_corruption("Foo.java", valid) is None


def test_find_structural_corruption_detects_unclosed_and_extra_braces():
    unclosed = "public class Foo {\n    void bar() {\n"
    problem = find_structural_corruption("Foo.java", unclosed)
    assert problem is not None
    assert "unclosed" in problem


def test_apply_anchored_edits_handles_search_block_with_different_blank_line_count_than_content():
    """Regression test for a real bug found live, 2026-08-11
    (kriya-oneshot-protocol-ignite-qpid audit): the OLD uniqueness check
    counted matches against a whole-file, ALL-blank-lines-collapsed flatten
    (normalize_whitespace(current_content).count(normalize_whitespace(search))),
    while actual application either needed an exact substring match or a
    window of EXACTLY len(search_block.splitlines()) raw lines. A search
    block with one blank line matched against content with two blank lines at
    the same location passed the old uniqueness check (it reported exactly 1
    match) but then failed application with "Could not find formatted match
    inside target content" - a self-contradictory outcome, confirmed by
    reproducing it against the pre-fix function. The fix computes uniqueness
    and location with the SAME window algorithm, so this must now succeed."""
    from kriya.workflow.workflow import apply_anchored_edits

    search_block = "int a = 1;\n\nint b = 2;"
    content = "class X {\n    int a = 1;\n\n\n    int b = 2;\n}"
    edits = [{"search": search_block, "replace": "int a = 99;\n\nint b = 2;"}]

    result = apply_anchored_edits(content, edits, "")

    assert "int a = 99;" in result
    assert "int b = 2;" in result


def test_apply_anchored_edits_still_rejects_genuinely_ambiguous_window_match():
    from kriya.workflow.workflow import apply_anchored_edits

    search_block = "return x;"
    content = "int a() { return x; }\nint b() { return x; }"
    edits = [{"search": search_block, "replace": "return y;"}]

    with pytest.raises(ValueError, match="matched 2 times"):
        apply_anchored_edits(content, edits, "")


def test_apply_anchored_edits_still_rejects_zero_matches():
    from kriya.workflow.workflow import apply_anchored_edits

    edits = [{"search": "does not exist anywhere", "replace": "x"}]

    with pytest.raises(ValueError, match="matched 0 times"):
        apply_anchored_edits("class X {}", edits, "")

    extra = "public class Foo {\n    void bar() {}\n}\n}\n"
    problem2 = find_structural_corruption("Foo.java", extra)
    assert problem2 is not None
    assert "extra closing" in problem2


def test_apply_anchored_edits_grounds_a_chained_edit_against_evolving_content():
    """Regression test for a real bug found live, 2026-08-17, digging into a
    corpus-wide survey of eval-harness runs: shown_context is a fixed
    snapshot passed in once, never updated across the per-edit loop, but
    current_content DOES evolve as earlier edits in the SAME response get
    applied. A two-step chained edit (edit #1 adds a `helper();` call, edit
    #2 wants to comment on that exact new line) is completely valid and
    internally consistent, but edit #2's search text was never part of the
    ORIGINAL file the model was shown - only of what edit #1 itself just
    introduced - so the old check (comparing only against the static
    shown_context) wrongly rejected it as "elided in the skeletonized
    context," even though the model introduced that exact text itself, one
    edit earlier in the same response. Confirmed live in the run history:
    14 occurrences of this exact rejection message across the eval-harness
    corpus, at least one with a multi-edit response ("edit #4") consistent
    with this shape."""
    from kriya.workflow.workflow import apply_anchored_edits

    original = "public class App {\n    int x = 1;\n}\n"
    shown_context = original
    edits = [
        {"search": "int x = 1;", "replace": "int x = 1;\n    helper();"},
        {"search": "helper();", "replace": "helper(); // step 2, chained off edit 1"},
    ]
    result = apply_anchored_edits(original, edits, shown_context)
    assert "helper(); // step 2, chained off edit 1" in result


def test_apply_anchored_edits_chained_grounding_still_rejects_fabricated_search_text():
    # Companion negative case - a search block that matches NEITHER the
    # original shown context NOR the evolving current content (a genuinely
    # fabricated/hallucinated block, not one introduced by an earlier edit
    # in this same response) must still be rejected.
    from kriya.workflow.workflow import apply_anchored_edits

    original = "public class App {\n    int x = 1;\n}\n"
    shown_context = original
    edits = [{"search": "<parameter name=\"totally_fake\"/>", "replace": "<property name=\"totally_fake\"/>"}]

    with pytest.raises(ValueError, match="elided in the skeletonized context"):
        apply_anchored_edits(original, edits, shown_context)


def test_find_structural_corruption_xml_well_formed_vs_malformed():
    valid_xml = "<beans><bean id=\"x\" class=\"Y\"/></beans>"
    assert find_structural_corruption("applicationContext.xml", valid_xml) is None

    malformed_xml = "<beans><bean id=\"x\" class=\"Y\"></beans>"  # unclosed <bean>
    problem = find_structural_corruption("applicationContext.xml", malformed_xml)
    assert problem is not None
    assert "malformed XML" in problem


def test_find_structural_corruption_ignores_other_extensions():
    # Deliberately not extended to Python/Ruby/JSON/etc. without a real
    # incident to justify it (see find_structural_corruption's own
    # docstring for why a real, zero-dependency ast.parse() check for
    # Python was tried and reverted during the 2026-08-12 SME review:
    # 100% overlap with the real Python compile gate, so it would silently
    # downgrade every Python syntax error's targeted retry from
    # content-shown to error-text-only, for zero cost savings).
    assert find_structural_corruption("app.py", "def f(:\n    pass") is None
    assert find_structural_corruption("app.rb", "def f(\n  end") is None


def test_find_structural_corruption_catches_a_complete_brace_balanced_duplicate_class():
    """Closes the one concretely-named still-open gap from the 2026-08-08
    incident (see test_find_structural_corruption_catches_the_real_duplicate_class_shape's
    own docstring): a *complete*, self-closed duplicate top-level class is
    brace-balanced by construction, so the brace-count check alone can never
    catch it - this is the variant that check explicitly could not."""
    duplicated = (
        "package com.example;\n\n"
        "public class ProtocolParser {\n"
        "    void encode() {}\n"
        "}\n"
        "\n"
        "package com.example;\n\n"
        "public class ProtocolParser {\n"
        "    void encode() {}\n"
        "}\n"
    )
    problem = find_structural_corruption("ProtocolParser.java", duplicated)
    assert problem is not None
    assert "duplicate top-level type" in problem
    assert "ProtocolParser" in problem


def test_find_structural_corruption_does_not_flag_a_legitimately_named_nested_class():
    valid = (
        "public class Outer {\n"
        "    static class Inner {\n"
        "        void run() {}\n"
        "    }\n"
        "}\n"
    )
    assert find_structural_corruption("Outer.java", valid) is None

def test_atomic_write_file_writes_correct_content(tmp_path):
    target = tmp_path / "App.java"
    atomic_write_file(str(target), "public class App {}\n")
    assert target.read_text() == "public class App {}\n"

def test_atomic_write_file_leaves_no_temp_file_behind(tmp_path):
    target = tmp_path / "App.java"
    atomic_write_file(str(target), "content")
    # Only the real target file should exist - no .kriya-tmp-<pid> leftover.
    assert [p.name for p in tmp_path.iterdir()] == ["App.java"]

def test_atomic_write_file_overwrites_existing_content_completely():
    """Regression test for the real bug this exists to fix (2026-08-16, live-
    observed): plain open(path, "w") truncates the file to empty BEFORE writing
    new content, so a process killed between the truncate and the write leaves
    0 bytes on disk with the old content already destroyed - confirmed live as
    a real eval-harness timeout leaving a previously-written file empty,
    destroying the one piece of evidence that would have explained a still-
    unresolved recurring compile failure. Can't directly test "survives an
    uncatchable SIGKILL mid-write" in a unit test, but this confirms the
    observable contract atomic_write_file guarantees: old content is replaced
    wholesale with new content, never left in a partial/mixed state - the
    os.replace() swap is what makes that atomic in practice."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        target = os.path.join(d, "App.java")
        atomic_write_file(target, "public class App { /* very long old content */ }\n" * 50)
        atomic_write_file(target, "public class App {}\n")
        with open(target) as f:
            assert f.read() == "public class App {}\n"


@pytest.mark.asyncio
async def test_workflow_structural_corruption_rejects_before_compiling(tmp_path):
    """End-to-end regression test for find_structural_corruption() as a
    defense-in-depth safety net, independent of the "Corrected file
    content for '...':" regex fix in kriya/agents/agent.py (workflow.py
    already runs every edit's search/replace through
    DeveloperAgent.sanitize_generated_content() before applying it, so that
    fix alone already neutralizes the exact "duplicate file dump" shape
    before it reaches this check - confirmed directly while building this
    test). This test instead uses a malformed-but-unrelated edit (a
    dangling, unclosed appended method - no "file content" phrase in it, so
    sanitization passes it through untouched) to prove the structural check
    still catches a corrupted write on its own. Confirmed here by asserting
    PolymorphicValidator's compile check is called exactly twice (attempt
    1's real failure, and the eventual real fix's success) - never for the
    rejected, structurally-corrupted attempt."""
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.paths.logs = str(tmp_path / "logs")
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write App.java",
        '[{"filepath": "App.java", "content": "class App {\\n  int x = 1;\\n}"}]',
        "Review: Approved",
    ])

    we = WorkflowEngine(kernel, llm)
    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check") as mock_compile:
        mock_compile.side_effect = [
            {"success": False, "output": "[ERROR] .../App.java:[2,11] some compile error"},
            {"success": True, "output": ""},
        ]
        we.developer.run_generation = AsyncMock(
            side_effect=[
                # Attempt 1: full content, fails compile above.
                [{"filepath": "App.java", "content": "class App {\n  int x = 1;\n}"}],
                # Attempt 2: an edit that applies cleanly (no anchor-match
                # failure) but its replace text is malformed - a dangling,
                # unclosed appended method - producing an unbalanced file.
                [{"filepath": "App.java", "edits": [{
                    "search": "  int x = 1;",
                    "replace": "  int x = 2;\n\n  public void extra() {\n",
                }]}],
                # Attempt 3: a real, clean fix.
                [{"filepath": "App.java", "content": "class App {\n  int x = 2;\n}"}],
            ]
        )
        res = await we.run_generation_workflow(goal="Create a Java app", workspace_path=str(tmp_path))

    assert res["quality_gates_passed"] is True
    assert mock_compile.call_count == 2  # attempt 2's rejection never reached compile

    db_path = os.path.join(cfg.paths.logs, "traces.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM runs ORDER BY timestamp DESC LIMIT 1").fetchone()
    conn.close()
    gate_outcomes = json.loads(row["gate_outcomes"])

    corruption_failures = [o for o in gate_outcomes if o.get("type") == "structural_corruption"]
    assert len(corruption_failures) == 1, gate_outcomes
    assert corruption_failures[0]["likely_files"] == ["App.java"]
    assert corruption_failures[0]["success"] is False


def test_incomplete_generation_error_carries_missing_files():
    err = IncompleteGenerationError(["Helper.java", "config.xml"], "INCOMPLETE GENERATION: ...")
    assert err.missing_files == ["Helper.java", "config.xml"]
    assert isinstance(err, ValueError)
    assert str(err) == "INCOMPLETE GENERATION: ..."


def test_build_missing_files_retry_prompt_frames_missing_and_reference_files(tmp_path):
    (tmp_path / "BrokerServer.java").write_text("class BrokerServer { /* written */ }")

    task_desc, context = _build_missing_files_retry_prompt(
        goal="Build the broker",
        plan="Write BrokerServer.java and BrokerConfig.java",
        design="## Files to Create\n- BrokerServer.java\n- BrokerConfig.java",
        missing_files=["BrokerConfig.java"],
        all_files_written=["BrokerServer.java"],
        worktree_path=str(tmp_path),
        active_code_context="=== base RAG context ===\n",
    )

    assert "MISSING-FILE recovery attempt" in task_desc
    assert "BrokerConfig.java" in task_desc
    assert "=== base RAG context ===" in context
    assert "=== Architect Design ===" in context
    assert "## Files to Create" in context
    assert "Existing file (already written, reference only" in context
    assert "class BrokerServer { /* written */ }" in context


def test_build_full_set_retry_prompt_includes_prior_attempt_content(tmp_path):
    """Regression test for a real bug found via the golden Ignite+Qpid use
    case: full-set retries previously never showed the model its own prior
    attempt's content, only the abstract error text - _build_targeted_retry_
    prompt's own docstring already named this exact gap. A "Dependency
    regression: ... you must preserve all existing dependencies" error alone
    doesn't tell the model WHAT those dependencies actually are, so a
    full-set regeneration (rewriting the file to match the current goal)
    kept dropping an existing, goal-irrelevant dependency it had no way to
    see. Confirmed live before fixing."""
    (tmp_path / "pom.xml").write_text("<project><artifactId>ignite-indexing</artifactId></project>")

    task_desc, context = _build_full_set_retry_prompt(
        goal="Add Qpid to the project",
        plan="Extend pom.xml and add QpidClientApp.java",
        error_context="Dependency regression: ignite-indexing was removed. You must preserve all existing dependencies.",
        required_files_prompt_block="\n\nFiles required: pom.xml, QpidClientApp.java",
        all_files_written=["pom.xml"],
        worktree_path=str(tmp_path),
        active_code_context="=== base RAG context ===\n",
    )

    assert "Dependency regression" in task_desc
    assert "Files required: pom.xml" in task_desc
    assert "=== base RAG context ===" in context
    assert "Your previous attempt's content for pom.xml" in context
    assert "ignite-indexing" in context


def test_build_full_set_retry_prompt_includes_required_dependencies_checklist(tmp_path):
    """Regression test: showing the model its own prior attempt's pom.xml
    content as passive reference material (the test above) was confirmed live
    NOT sufficient on its own to stop dependency drops from recurring across
    repeated full-set retries - an explicit "preserve these" checklist is
    needed, mirroring required_files_prompt_block's already-proven pattern."""
    task_desc, _ = _build_full_set_retry_prompt(
        goal="Add Qpid to the project",
        plan="Extend pom.xml and add QpidClientApp.java",
        error_context="Dependency regression: ignite-indexing was removed.",
        required_files_prompt_block="",
        all_files_written=["pom.xml"],
        worktree_path=str(tmp_path),
        active_code_context="=== base RAG context ===\n",
        required_dependencies_prompt_block=(
            "\n\nExisting Maven dependencies: preserve these:\n"
            "- org.apache.ignite:ignite-core\n"
            "- org.apache.ignite:ignite-indexing"
        ),
    )
    assert "Existing Maven dependencies: preserve these:" in task_desc
    assert "org.apache.ignite:ignite-indexing" in task_desc


def test_build_full_set_retry_prompt_empty_when_nothing_written_yet(tmp_path):
    # The very first attempt (retry_count == 0) has nothing in all_files_written
    # yet - must behave exactly like the old unconditional task_desc/context,
    # not error out or add spurious content.
    task_desc, context = _build_full_set_retry_prompt(
        goal="Build the broker",
        plan="Write BrokerServer.java",
        error_context="",
        required_files_prompt_block="",
        all_files_written=[],
        worktree_path=str(tmp_path),
        active_code_context="=== base RAG context ===\n",
    )

    assert task_desc == "Goal: Build the broker\nPlan: Write BrokerServer.java"
    assert context == "=== base RAG context ===\n\n\n"


@pytest.mark.asyncio
async def test_workflow_missing_file_recovery_does_not_escalate_or_consume_fullset_budget(tmp_path):
    """A completeness-check failure must trigger a missing-file recovery retry on
    the very next attempt - primary model only, even with a fallback chain
    configured - not a full-file-set regeneration and not model escalation,
    mirroring the existing implicated-file targeted retry's no-escalation budget.

    Note on how this is now triggered: since the initial generation call passes
    the Architect's own deterministic file list as known_target_files whenever
    one is available, the model can no longer cause this by simply omitting a
    file from its own file-list response - known_target_files guarantees every
    expected file gets a write attempt on attempt 1. The remaining, still-real
    way a file can end up "missing" despite being targeted is a downstream
    rejection by normalize_written_filepath (e.g. a resolved path that turns out
    to be unusable) - simulated here for exactly one file's first write, to prove
    the recovery mechanism itself is still correct now that its only reachable
    trigger has narrowed."""
    from kriya.config import FallbackModelConfig
    from kriya.workflow.workflow import normalize_written_filepath as real_normalize_written_filepath
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.llm_chain = [FallbackModelConfig(model="fallback-1")]
    cfg.paths.skills = str(tmp_path / "skills")
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    prompts_seen = []

    async def mock_complete(*args, **kwargs):
        prompts_seen.append((args[1] if len(args) > 1 else None, kwargs.get("model_override")))
        n = len(prompts_seen)
        if n == 1:
            return "Step 1: Write code"
        elif n == 2:
            return "Design: Write math.py and helper.py"
        elif n == 3:
            # Attempt 1, first of two known_target_files-driven per-file calls
            # (sorted order: helper.py before math.py).
            return "def helper():\n    pass"
        elif n == 4:
            return "def add(a,b):\n    return a+b"
        elif n == 5:
            # Missing-file recovery retry: writes the rejected-then-retried file.
            return "def helper():\n    pass"
        else:
            return "Review: Approved"

    llm.complete = mock_complete

    # Simulate normalize_written_filepath rejecting helper.py's first write only -
    # real behavior otherwise, so this is a narrow, targeted simulation of the
    # one remaining real trigger, not a blanket bypass of the actual function.
    rejected_once = {"done": False}
    def flaky_normalize(filepath, workspace_path):
        if filepath == "helper.py" and not rejected_once["done"]:
            rejected_once["done"] = True
            return None
        return real_normalize_written_filepath(filepath, workspace_path)

    we = WorkflowEngine(kernel, llm)
    with patch("kriya.workflow.attempt.normalize_written_filepath", side_effect=flaky_normalize):
        res = await we.run_generation_workflow(
            goal="Create math library with a helper module", workspace_path=str(tmp_path)
        )

    assert res["quality_gates_passed"] is True
    assert "helper.py" in res["files"]
    # Attempt 2 (index 4) must be the missing-file recovery retry: primary model
    # (no override, despite a configured fallback chain) and a prompt that names
    # the specific missing file, not a generic full-set regeneration.
    recovery_prompt, recovery_model_override = prompts_seen[4]
    assert recovery_model_override is None
    assert "MISSING-FILE recovery attempt" in recovery_prompt
    assert "helper.py" in recovery_prompt


def test_write_skill_extraction_never_overwrites_existing_example(tmp_path):
    """Regression test for a real bug caught live: a skill-gap/live-lookup extraction
    writing a new 'examples' entry whose basename matches an already-existing example
    file used to silently overwrite it - destroying previously-curated content (a real
    exec-maven-plugin pom.xml example was clobbered by a bare-dependencies-only
    version during live testing). Existing example files must never be overwritten by
    extraction - only genuinely new filenames get written."""
    from kriya.skills.skill import Skill
    from kriya.workflow.workflow import _write_skill_extraction

    _init_git_repo(tmp_path)
    skill_dir = tmp_path / "myskill"
    examples_dir = skill_dir / "examples"
    examples_dir.mkdir(parents=True)
    (examples_dir / "pom.xml").write_text("ORIGINAL CURATED CONTENT")

    skill = Skill(name="myskill", description="test", source_path=str(skill_dir))

    _write_skill_extraction(
        skill,
        {"examples": {"pom.xml": "OVERWRITTEN BY EXTRACTION", "new_file.txt": "brand new content"}},
        source="test",
    )

    assert (examples_dir / "pom.xml").read_text() == "ORIGINAL CURATED CONTENT"
    assert (examples_dir / "new_file.txt").read_text() == "brand new content"


def test_write_skill_extraction_collapses_embedded_newlines_in_a_rule(tmp_path):
    """Regression test for a finding from the 2026-08-12 SME review:
    rules.txt is written with no newline-stripping/escaping, but every
    reader (SkillEngine.discover_and_load, kriya/cli.py's skills commands)
    parses strictly line-by-line - an embedded newline in extracted rule
    text silently split into multiple unrelated "rules" with no error."""
    from kriya.skills.skill import Skill
    from kriya.workflow.workflow import _write_skill_extraction

    skill_dir = tmp_path / "myskill"
    skill_dir.mkdir()
    skill = Skill(name="myskill", description="test", source_path=str(skill_dir))

    _write_skill_extraction(
        skill,
        {"rules": ["Always call close() on the client.\nNever skip this step."]},
        source="test",
    )

    lines = [
        line.strip() for line in (skill_dir / "rules.txt").read_text().splitlines() if line.strip()
    ]
    assert lines == ["Always call close() on the client. Never skip this step."]


def test_stage_skill_conflicts_collapses_embedded_newlines(tmp_path):
    from kriya.skills.skill import Skill
    from kriya.workflow.workflow import _stage_skill_conflicts

    skill_dir = tmp_path / "myskill"
    skill_dir.mkdir()
    skill = Skill(name="myskill", description="test", source_path=str(skill_dir))

    _stage_skill_conflicts(skill, [{
        "candidate_rule": "Use a fixed\nthread pool.",
        "conflicts_with": "Use a cached\nthread pool.",
        "reason": "contradicts\nexisting guidance",
    }])

    lines = [
        line.strip() for line in (skill_dir / "staged_rules.txt").read_text().splitlines() if line.strip()
    ]
    assert lines == [
        "[CONFLICT] Use a fixed thread pool. -- conflicts with existing rule: "
        "'Use a cached thread pool.' (contradicts existing guidance)"
    ]


def test_create_git_worktree_carries_over_uncommitted_changes(tmp_path):
    """Regression test for a real bug caught live: create_git_worktree only ever
    reflected git HEAD, so any uncommitted work in the real workspace (the normal
    state of an in-progress project) was invisible inside the sandbox - a goal
    building additively on a previous uncommitted change failed every retry with
    confusing "package does not exist" errors, since the file it was told to
    preserve/extend simply didn't exist in the sandbox at all."""
    from kriya.workflow.workflow import create_git_worktree

    _init_git_repo(tmp_path)

    # Modify a tracked file without committing.
    (tmp_path / "README.md").write_text("modified but uncommitted\n")
    # Add a brand-new untracked file.
    (tmp_path / "pom.xml").write_text("<project>uncommitted new file</project>\n")

    worktree_path = create_git_worktree(str(tmp_path))

    readme = open(os.path.join(worktree_path, "README.md")).read()
    assert readme == "modified but uncommitted\n"
    pom = open(os.path.join(worktree_path, "pom.xml")).read()
    assert pom == "<project>uncommitted new file</project>\n"


def test_create_git_worktree_carries_over_a_wholly_untracked_directory(tmp_path):
    """Regression test for a real bug caught live: `git status --porcelain` in its
    default mode collapses an ENTIRELY untracked directory into a single line
    (`?? src/`) rather than one line per file, so the untracked-file sync loop's
    `if os.path.isdir(src): continue` silently dropped the whole directory - a
    from-scratch `src/` tree (regenerated by a prior run but never git-committed)
    never reached the worktree sandbox at all, producing a "package does not
    exist" compile failure for source files a goal explicitly asked to preserve,
    with nothing about it looking like a sandboxing gap rather than a model
    mistake. A single untracked file (already covered by the sibling test above)
    never collapses this way - only a directory with zero committed content
    anywhere under it does, which is why that test alone didn't catch this."""
    from kriya.workflow.workflow import create_git_worktree

    _init_git_repo(tmp_path)

    nested_dir = tmp_path / "src" / "main" / "java" / "com" / "example" / "protocol"
    nested_dir.mkdir(parents=True)
    (nested_dir / "Protocol.java").write_text("package com.example.protocol;\npublic class Protocol {}\n")
    (nested_dir / "ProtocolParser.java").write_text("package com.example.protocol;\npublic class ProtocolParser {}\n")

    worktree_path = create_git_worktree(str(tmp_path))

    protocol = open(os.path.join(worktree_path, "src", "main", "java", "com", "example", "protocol", "Protocol.java")).read()
    assert "public class Protocol" in protocol
    parser = open(os.path.join(worktree_path, "src", "main", "java", "com", "example", "protocol", "ProtocolParser.java")).read()
    assert "public class ProtocolParser" in parser


def test_create_git_worktree_removes_files_deleted_in_working_tree(tmp_path):
    """A file deleted (uncommitted) in the real workspace must not linger as a stale
    HEAD-only copy in the worktree sandbox."""
    from kriya.workflow.workflow import create_git_worktree

    _init_git_repo(tmp_path)
    (tmp_path / "extra.txt").write_text("will be committed then deleted\n")
    subprocess.run(["git", "add", "extra.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "add extra.txt"], cwd=tmp_path, check=True)
    os.remove(tmp_path / "extra.txt")

    worktree_path = create_git_worktree(str(tmp_path))

    assert not os.path.exists(os.path.join(worktree_path, "extra.txt"))


def test_create_git_worktree_reset_advances_to_new_commits_on_reuse(tmp_path):
    """Regression test for a real bug caught live: a worktree created via
    `git worktree add --detach` gets its own fixed HEAD pointer from creation
    time, not a moving ref - so `git checkout -f HEAD` run to "reset" it on a
    later, separate create_git_worktree call (a real, normal case: a second
    `generate` invocation on the same project) resolved against the worktree's
    own frozen pointer and was a no-op. Any commit landed on the real repo
    after the worktree's first creation became permanently invisible to the
    sandbox - confirmed live: a goal explicitly telling the model to preserve
    an already-committed pom.xml correctly didn't rewrite it, so the sandbox
    compiled with a `pom.xml` that had reverted to a state from before ANY of
    that project's real work existed, failing every import."""
    from kriya.workflow.workflow import create_git_worktree

    _init_git_repo(tmp_path)

    # First creation - detaches the worktree at this initial commit.
    worktree_path = create_git_worktree(str(tmp_path))
    assert not os.path.exists(os.path.join(worktree_path, "pom.xml"))

    # A separate, later `generate` invocation commits new content to the real
    # repo (e.g. a prior milestone's output actually being applied+committed).
    (tmp_path / "pom.xml").write_text("<project>committed after worktree creation</project>\n")
    subprocess.run(["git", "add", "pom.xml"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "add pom.xml"], cwd=tmp_path, check=True)

    # Reusing the same (already-registered) worktree must pick up that commit,
    # not silently stay frozen at the original creation-time commit.
    worktree_path_again = create_git_worktree(str(tmp_path))
    assert worktree_path_again == worktree_path
    pom = open(os.path.join(worktree_path, "pom.xml")).read()
    assert pom == "<project>committed after worktree creation</project>\n"


def test_remove_git_worktree_resets_to_current_commit_not_creation_time_commit(tmp_path):
    """Same underlying bug as the reset-on-reuse case above, but for
    remove_git_worktree specifically: despite the name, it does not actually
    delete/unregister the worktree (by design - it's reused across runs so
    compile caches survive), it only resets it - and that reset had the exact
    same "HEAD resolves against itself" no-op bug."""
    from kriya.workflow.workflow import create_git_worktree, remove_git_worktree

    _init_git_repo(tmp_path)
    worktree_path = create_git_worktree(str(tmp_path))

    (tmp_path / "pom.xml").write_text("<project>added after worktree creation</project>\n")
    subprocess.run(["git", "add", "pom.xml"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "add pom.xml"], cwd=tmp_path, check=True)

    remove_git_worktree(str(tmp_path), worktree_path)

    assert os.path.exists(os.path.join(worktree_path, "pom.xml"))


@pytest.mark.asyncio
async def test_workflow_multi_skill_gap_prompt_does_not_misattribute_extraction(tmp_path):
    """Regression test for a real bug caught live: when a single skill-gap prompt
    covers MULTIPLE unverified skills at once, a single human-supplied reference
    used to get extraction-attempted against EVERY co-flagged skill using the same
    ambiguous multi-skill gap description - Ignite-specific reference material
    supplied for a combined "qpid, ignite-java17" gap got Ignite content written
    into qpid/rules.txt. This simulates the worst case directly: even if the
    (mocked) extraction call misbehaves IDENTICALLY for both co-flagged skills (as
    if the scoped-prompt fix had no effect at all), the deterministic code-level
    guard (_filter_misattributed_extraction) must still keep the misattributed
    content out of the wrong skill and only apply it to the one it's actually about."""
    from kriya.skills.skill import SkillEngine

    skills_dir = tmp_path / "skills"
    broker_dir = skills_dir / "acmebroker"
    broker_dir.mkdir(parents=True)
    (broker_dir / "skill.yaml").write_text("name: acmebroker\ndescription: Test\ntags: [acmebroker]\n")
    (broker_dir / "rules.txt").write_text("")

    cache_dir = skills_dir / "acmecache"
    cache_dir.mkdir(parents=True)
    (cache_dir / "skill.yaml").write_text("name: acmecache\ndescription: Test\ntags: [acmecache]\n")
    (cache_dir / "rules.txt").write_text("")

    cfg = AppConfig()
    cfg.paths.skills = str(skills_dir)
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    cache_flavored_extraction = json.dumps({
        "rules": ["Use acmecache.start() to begin caching; acmecache handles get/put operations."],
        "examples": {}, "conflicts": []
    })

    llm.complete = AsyncMock(side_effect=[
        cache_flavored_extraction,  # extraction call for the first co-flagged skill
        cache_flavored_extraction,  # extraction call for the second co-flagged skill
        "Step 1: Write code",  # Planner
        "Design: Write app.py",  # Architect
        json.dumps([{"filepath": "app.py", "content": "print('ok')\n"}]),  # Developer
        "Review: Approved",  # Reviewer
    ])

    def skill_gap_cb(reason, names):
        assert set(names) == {"acmebroker", "acmecache"}
        return "Use acmecache.start() to begin caching; acmecache handles get/put operations."

    we = WorkflowEngine(kernel, llm)
    await we.run_generation_workflow(
        goal="Build an app using acmebroker and acmecache",
        workspace_path=str(tmp_path),
        skill_gap_callback=skill_gap_cb,
    )

    se = SkillEngine(str(skills_dir), load_global=False)
    se.discover_and_load()
    broker_skill = se.get_skill("acmebroker")
    cache_skill = se.get_skill("acmecache")

    assert broker_skill.rules == []
    assert "Use acmecache.start() to begin caching; acmecache handles get/put operations." in cache_skill.rules


def _make_skill(name, tags):
    from kriya.skills.skill import Skill
    return Skill(name=name, description="test", tags=tags)

def test_scoped_skill_gap_description_names_only_the_one_skill():
    desc = _scoped_skill_gap_description("qpid")
    assert "qpid" in desc
    assert "ignite" not in desc.lower()

def test_likely_misattributed_sibling_catches_real_observed_case():
    """Uses the real qpid/ignite-java17 tag sets and the actual kind of Ignite
    content that was misattributed into qpid/rules.txt live this session."""
    qpid = _make_skill("qpid", ["qpid", "qpid-jms", "qpid-broker-j", "redhat-mrg", "mrg", "amqp-1.0"])
    ignite = _make_skill("ignite-java17", ["ignite", "apache-ignite", "ignite-cache"])
    misattributed_rule = (
        "Use Apache Ignite 2.18.0 with Java 17 source/target and add the required "
        "--add-opens JVM flags. Bootstrap Ignite from Spring XML using either "
        "Ignition.start(\"ignite-config.xml\") or by declaring an "
        "org.apache.ignite.IgniteSpringBean in the XML."
    )
    assert _likely_misattributed_sibling(misattributed_rule, qpid, [ignite]) == "ignite-java17"
    # And the reverse direction: genuinely qpid content targeted at ignite-java17.
    qpid_rule = (
        "Embed the broker via org.apache.qpid.server.SystemLauncher: call "
        "startup(Map<String,Object>) with a config map containing \"type\": \"Memory\"."
    )
    assert _likely_misattributed_sibling(qpid_rule, ignite, [qpid]) == "qpid"

def test_likely_misattributed_sibling_accepts_genuinely_on_topic_content():
    qpid = _make_skill("qpid", ["qpid", "qpid-jms", "qpid-broker-j", "redhat-mrg", "mrg", "amqp-1.0"])
    ignite = _make_skill("ignite-java17", ["ignite", "apache-ignite", "ignite-cache"])
    on_topic_qpid_rule = (
        "Use org.apache.qpid:qpid-broker-core, org.apache.qpid:qpid-broker-plugins-amqp-1-0-protocol, "
        "and org.apache.qpid:qpid-broker-plugins-memory-store (version 9.2.1) for an embedded Qpid Broker-J server."
    )
    assert _likely_misattributed_sibling(on_topic_qpid_rule, qpid, [ignite]) is None

def test_likely_misattributed_sibling_no_siblings_never_flags():
    qpid = _make_skill("qpid", ["qpid"])
    assert _likely_misattributed_sibling("Use Apache Ignite for caching.", qpid, []) is None

def test_likely_misattributed_sibling_abstains_when_target_identity_is_all_stopwords():
    """Regression test for a finding from the 2026-08-12 SME review: a
    target skill whose entire name+tags are composed only of generic
    stoplist words (e.g. a bare "java" skill - "java" itself is in
    _IDENTITY_GENERIC_WORDS) has an EMPTY identity word set, so the "target's
    own words present" short-circuit could never fire for it - every rule
    extracted for this skill got checked ONLY against siblings, and any
    incidental word overlap wrongly flagged genuinely on-topic content as
    misattributed. An empty target identity must abstain (return None)
    instead of becoming maximally aggressive."""
    bare_java = _make_skill("java", ["java"])
    other = _make_skill("spring-boot", ["spring", "spring-boot"])
    genuinely_on_topic_rule = "Use try-with-resources to ensure the Spring context closes cleanly."
    assert _likely_misattributed_sibling(genuinely_on_topic_rule, bare_java, [other]) is None

def test_filter_misattributed_extraction_drops_only_the_misattributed_entries():
    qpid = _make_skill("qpid", ["qpid", "qpid-jms", "qpid-broker-j", "redhat-mrg", "mrg", "amqp-1.0"])
    ignite = _make_skill("ignite-java17", ["ignite", "apache-ignite", "ignite-cache"])
    extraction = {
        "rules": [
            "Use org.apache.qpid:qpid-broker-core for an embedded Qpid Broker-J server.",
            "Bootstrap Ignite from Spring XML using Ignition.start(\"ignite-config.xml\").",
        ],
        "examples": {
            "qpid-initial-config.json": "{\"name\": \"EmbeddedBroker\"}",
            "ignite-config.xml": "<bean class=\"org.apache.ignite.IgniteSpringBean\"/>",
        },
        "conflicts": [],
    }
    filtered = _filter_misattributed_extraction(extraction, qpid, [ignite])
    assert filtered["rules"] == ["Use org.apache.qpid:qpid-broker-core for an embedded Qpid Broker-J server."]
    assert list(filtered["examples"].keys()) == ["qpid-initial-config.json"]


@pytest.mark.asyncio
async def test_workflow_gate_outcome_records_attribution_tier(tmp_path):
    """attribute_failure() (kriya/workflow/attribution.py) persists which
    tier decided a failure's implicated file(s) onto the gate_outcome - same
    graded_by-style pattern already used for the verification-contract-
    reliability fix, so a live run can be audited for which attribution tier
    actually fired instead of just trusted. A locator-bearing compile error
    (a real javac-style File.java:[line,col] locator) should record
    tier="locator"/confidence="high" and correctly scope the very next
    attempt to a targeted retry of just that file."""
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.paths.logs = str(tmp_path / "logs")
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write App.java",
        "Review: Approved",
    ])

    we = WorkflowEngine(kernel, llm)
    we.developer.run_generation = AsyncMock(side_effect=[
        [{"filepath": "App.java", "content": "class App {\n  Object x;\n}"}],
        [{"filepath": "App.java", "content": "class App {\n  String x;\n}"}],
    ])

    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check") as mock_compile:
        mock_compile.side_effect = [
            {"success": False, "output": "[ERROR] .../App.java:[2,3] cannot find symbol"},
            {"success": True, "output": ""},
        ]
        res = await we.run_generation_workflow(goal="Create a Java app", workspace_path=str(tmp_path))

    assert res["quality_gates_passed"] is True
    assert we.developer.run_generation.call_count == 2
    second_call_kwargs = we.developer.run_generation.call_args_list[1].kwargs
    assert second_call_kwargs["known_target_files"] == ["App.java"]

    db_path = os.path.join(cfg.paths.logs, "traces.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM runs ORDER BY timestamp DESC LIMIT 1").fetchone()
    conn.close()
    gate_outcomes = json.loads(row["gate_outcomes"])
    compile_failure = next(g for g in gate_outcomes if g["type"] == "compile" and g["success"] is False)
    assert compile_failure["attribution_tier"] == "locator"
    assert compile_failure["attribution_confidence"] == "high"


@pytest.mark.asyncio
async def test_workflow_self_diagnosis_redirects_after_confirmed_repeat_failure(tmp_path):
    """End-to-end regression test for the self-diagnosis-feedback fix
    (2026-08-13, motivated by a real divergence found live in
    spikes/eval_harness/runs/attribution-fix-validation-2): a stack-trace
    locator kept a real run's retries stuck targeting the file where an
    exception was THROWN, even after the model's own FIX ANALYSIS correctly
    named a different file as the real cause. When a targeted retry's own
    analysis diverges from what it was asked to fix, and the SAME failure
    signature recurs, the NEXT attempt must redirect to the self-diagnosed
    file instead of re-deriving the same (wrong) locator forever."""
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.paths.logs = str(tmp_path / "logs")
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write A.java and B.java",
        "Review: Approved",
    ])

    we = WorkflowEngine(kernel, llm)
    we.developer.run_generation = AsyncMock(side_effect=[
        # Attempt 1 (full-set): writes both files.
        [
            {"filepath": "A.java", "content": "class A {\n  Object x;\n}"},
            {"filepath": "B.java", "content": "class B {}"},
        ],
        # Attempt 2 (targeted retry of A.java, driven by the locator below) -
        # the model's own analysis says the real cause is in B.java.
        [{
            "filepath": "A.java", "content": "class A {\n  Object x2;\n}",
            "analysis": "A.java itself is fine - the real problem is in B.java's own field type.",
        }],
        # Attempt 3 - should now be targeted at B.java via self_diagnosis,
        # not another blind re-try of A.java.
        [{"filepath": "B.java", "content": "class B { int y; }"}],
    ])

    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check") as mock_compile:
        mock_compile.side_effect = [
            {"success": False, "output": "[ERROR] .../A.java:[2,3] cannot find symbol"},
            {"success": False, "output": "[ERROR] .../A.java:[2,3] cannot find symbol"},  # identical - same signature
            {"success": True, "output": ""},
        ]
        res = await we.run_generation_workflow(goal="Create a Java app", workspace_path=str(tmp_path))

    assert res["quality_gates_passed"] is True
    assert we.developer.run_generation.call_count == 3
    third_call_kwargs = we.developer.run_generation.call_args_list[2].kwargs
    assert third_call_kwargs["known_target_files"] == ["B.java"]

    db_path = os.path.join(cfg.paths.logs, "traces.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM runs ORDER BY timestamp DESC LIMIT 1").fetchone()
    conn.close()
    gate_outcomes = json.loads(row["gate_outcomes"])
    second_failure = [g for g in gate_outcomes if g["type"] == "compile" and g["success"] is False][1]
    assert second_failure["attribution_tier"] == "self_diagnosis"
    assert second_failure["likely_files"] == ["B.java"]


@pytest.mark.asyncio
async def test_workflow_diagnosis_mismatch_redirects_back_to_the_same_file(tmp_path):
    """End-to-end regression test for the diagnosis-vs-diff check (2026-08-13,
    motivated by a real divergence found live in spikes/eval_harness/runs/
    attribution-fix-validation-3): a targeted retry's own FIX ANALYSIS
    correctly named a specific fix, but the returned edit never actually made
    that change.

    UPDATED 2026-08-17: a compile-triggered diagnosis-mismatch is now
    deliberately BYPASSED (see attempt.py's _diagnosis_mismatch_bypass_reason)
    rather than rejected pre-flight - found live that this specific check
    produced 3 confirmed false positives in one night, each wrongly rejecting
    an edit that would have compiled correctly, while this check's own
    original motivating incident (reproduced here) costs at most one extra,
    otherwise-identical compile failure if the check is bypassed instead of
    firing - the SAME repeated-failure signal the retry loop already tracks
    independently. The no-op edit below now proceeds all the way to the real
    compiler, fails there (for the same underlying reason, not a heuristic
    guess), and the next targeted retry is still correctly scoped back to
    A.java - via the real compiler's own locator this time, not the
    diagnosis-mismatch pre-flight check."""
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.paths.logs = str(tmp_path / "logs")
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write A.java",
        "Review: Approved",
    ])

    we = WorkflowEngine(kernel, llm)
    we.developer.run_generation = AsyncMock(side_effect=[
        # Attempt 1 (full-set): a real compile error with a locator for A.java.
        [{"filepath": "A.java", "content": "class A {\n  var cache = get();\n}"}],
        # Attempt 2 (targeted retry) - analysis names a specific fix, but the
        # edit doesn't actually make it (reproduces the real captured
        # validation-3 divergence). No longer caught pre-flight - proceeds to
        # the real compiler, which fails again for the same reason.
        [{
            "filepath": "A.java", "content": None,
            "edits": [{"search": "var cache = get();", "replace": "var cache = get(); // still untyped"}],
            "analysis": "the fix requires declaring `IgniteCache<Integer, Protocol>` explicitly instead of `var`.",
        }],
        # Attempt 3 (targeted retry again, redirected back to A.java by the
        # REAL compiler's own locator on attempt 2's failure) - this time the
        # fix is actually made.
        [{"filepath": "A.java", "content": "class A {\n  IgniteCache<Integer, Protocol> cache = get();\n}"}],
    ])

    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check") as mock_compile:
        mock_compile.side_effect = [
            {"success": False, "output": "[ERROR] .../A.java:[2,3] cannot find symbol"},
            # Attempt 2 now genuinely reaches the compile gate (the pre-flight
            # check is bypassed for a compile-triggered retry) and fails for
            # the same underlying reason, since the edit never actually fixed it.
            {"success": False, "output": "[ERROR] .../A.java:[2,3] cannot find symbol"},
            {"success": True, "output": ""},
        ]
        res = await we.run_generation_workflow(goal="Create a Java app", workspace_path=str(tmp_path))

    assert res["quality_gates_passed"] is True
    assert we.developer.run_generation.call_count == 3
    assert mock_compile.call_count == 3  # confirms attempt 2 DID reach the real compile gate
    third_call_kwargs = we.developer.run_generation.call_args_list[2].kwargs
    assert third_call_kwargs["known_target_files"] == ["A.java"]

    db_path = os.path.join(cfg.paths.logs, "traces.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM runs ORDER BY timestamp DESC LIMIT 1").fetchone()
    conn.close()
    gate_outcomes = json.loads(row["gate_outcomes"])
    # No diagnosis_mismatch outcome anymore - both attempt-1 and attempt-2
    # failures are genuine "compile" gate outcomes now (a third, successful
    # "compile" outcome also exists for attempt 3 - not a failure).
    assert not any(g["type"] == "diagnosis_mismatch" for g in gate_outcomes)
    failed_compile_outcomes = [g for g in gate_outcomes if g["type"] == "compile" and g.get("success") is False]
    assert len(failed_compile_outcomes) == 2


# =====================================================================
# PlannerAgent SME review fixes (2026-08-15): finding 1 (output
# completeness check) and finding 2 (skill-conventions reminder) - see
# docs/kriya_backlog_and_lessons.md's matching dated entry.
# =====================================================================

def test_check_plan_completeness_flags_empty_plan():
    assert check_plan_completeness("") is not None
    assert check_plan_completeness("   \n  ") is not None


def test_check_plan_completeness_accepts_a_short_but_nonempty_plan():
    """Regression test: the first version of this check used a minimum-
    length threshold, which broke 103 of 121 pre-existing tests in this
    file whose Planner-position mock is a short placeholder like this one -
    those tests exercise other stages, not plan content, and were never
    meant to look like a real plan. A short-but-real, non-empty response
    with no unclosed fence must be accepted."""
    assert check_plan_completeness("Step 1: do it") is None


def test_check_plan_completeness_flags_unclosed_code_fence():
    """Confirmed live, 2026-08-15 (spikes/model_speed_poc/planner_reasoning_poc.py,
    a real ignite_qpid_protocol run): a plan that runs out of token budget
    mid-response leaves an odd number of ``` markers - the actual shape both
    arms of that run hit, not a hypothetical."""
    plan = "A" * 150 + "\n```python\ndef foo():\n    pass\n"
    assert check_plan_completeness(plan) is not None


def test_check_plan_completeness_accepts_a_complete_plan():
    plan = "A" * 150 + "\n```python\ndef foo():\n    pass\n```\n"
    assert check_plan_completeness(plan) is None


@pytest.mark.asyncio
async def test_planner_prompt_includes_skill_conventions_reminder(tmp_path):
    """SME review finding 2: Planner receives convention_prompt the same as
    Architect/Developer, but nothing told it to actually use that content -
    the same "passive reference material is never enough" gap already fixed
    for Developer's own skill_reminder (test_agents.py's
    test_fill_missing_content_repeats_skill_conventions_reminder_at_end).
    Mirrors that test's shape at the workflow level, since PlannerAgent has
    no per-call prompt-building method of its own to unit-test directly."""
    skills_dir = tmp_path / "skills"
    skill_folder = skills_dir / "widgetlib"
    skill_folder.mkdir(parents=True)
    (skill_folder / "skill.yaml").write_text("name: widgetlib\ndescription: Test\ntags: [widgetlib]\n")
    (skill_folder / "rules.txt").write_text("Widgets must be printed in uppercase.\n")

    cfg = AppConfig()
    cfg.paths.skills = str(skills_dir)
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    # return_value, not a side_effect list: the real pipeline makes more
    # than 4 calls (Planner, Architect, Developer, RunVerifier.judge(),
    # Reviewer - judge() isn't obvious from the goal text alone) - a fixed
    # side_effect list of 4 here originally caused a real StopAsyncIteration
    # failure once actually run. Only call #1 (Planner) is inspected below,
    # so a single response safe for every stage to receive avoids having to
    # track the exact real call count at all.
    llm.complete = AsyncMock(return_value="OK")
    we = WorkflowEngine(kernel, llm)

    await we.run_generation_workflow(
        goal="Build a widget printer; the widgetlib skill applies here",
        workspace_path=str(tmp_path)
    )

    planner_prompt = llm.complete.call_args_list[0][0][1]
    conventions_pos = planner_prompt.index("Engineering Skill Conventions: widgetlib")
    reminder_pos = planner_prompt.index("apply the Engineering Skill Conventions above")
    assert reminder_pos > conventions_pos


@pytest.mark.asyncio
async def test_planner_prompt_no_skill_reminder_without_active_skills(tmp_path):
    # No-op when no skill matched this generation at all - never pay for a
    # reminder pointing at content that isn't there (same guarantee as
    # Developer's own test_fill_missing_content_no_skill_reminder_without_active_skills).
    cfg = AppConfig()
    cfg.paths.skills = str(tmp_path / "skills")
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    # return_value, not a side_effect list - see the sibling test above for
    # why a fixed-length list is fragile here (real call count is more than
    # 4 and isn't obvious from the goal text alone).
    llm.complete = AsyncMock(return_value="OK")
    we = WorkflowEngine(kernel, llm)

    await we.run_generation_workflow(goal="Print hello", workspace_path=str(tmp_path))

    planner_prompt = llm.complete.call_args_list[0][0][1]
    assert "apply the Engineering Skill Conventions above" not in planner_prompt


@pytest.mark.asyncio
async def test_workflow_stops_early_when_planner_output_is_truncated(tmp_path):
    """SME review finding 1: PlannerAgent had zero output-sanity check - a
    truncated/empty plan flowed silently into Architect with nothing
    detecting or explaining why. Confirms the fix stops the run cleanly
    (not a crash, same early-return shape as the KnowledgeGuard gap check)
    BEFORE Architect is ever called."""
    cfg = AppConfig()
    cfg.paths.skills = str(tmp_path / "skills")
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    # Empty string specifically - the real, originally-observed incident
    # (a reasoning model burning its whole token budget on invisible
    # thinking, confirmed live earlier this session) returned literally
    # nothing, not a short-but-real response. Same empty response every
    # call if the fix regresses and Architect gets called anyway -
    # call_count is the real signal here, not the content.
    llm.complete = AsyncMock(return_value="")

    we = WorkflowEngine(kernel, llm)

    res = await we.run_generation_workflow(goal="Build something", workspace_path=str(tmp_path))

    assert res["status"] == "planner_output_incomplete"
    assert "reason" in res
    assert llm.complete.call_count == 1


@pytest.mark.asyncio
async def test_architect_prompt_includes_skill_conventions_reminder(tmp_path):
    """SME review finding, Architect stage (2026-08-15): same gap as
    Planner's finding 2 - Architect receives the identical convention_prompt
    content Planner does, but nothing told it to actually use it either."""
    skills_dir = tmp_path / "skills"
    skill_folder = skills_dir / "widgetlib"
    skill_folder.mkdir(parents=True)
    (skill_folder / "skill.yaml").write_text("name: widgetlib\ndescription: Test\ntags: [widgetlib]\n")
    (skill_folder / "rules.txt").write_text("Widgets must be printed in uppercase.\n")

    cfg = AppConfig()
    cfg.paths.skills = str(skills_dir)
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    # return_value, not a side_effect list - see the Planner-reminder tests
    # above for why a fixed-length list is fragile here (real call count is
    # more than 4 and isn't obvious from the goal text alone). Only call #2
    # (Architect) is inspected below.
    llm.complete = AsyncMock(return_value="OK")
    we = WorkflowEngine(kernel, llm)

    await we.run_generation_workflow(
        goal="Build a widget printer; the widgetlib skill applies here",
        workspace_path=str(tmp_path)
    )

    design_prompt = llm.complete.call_args_list[1][0][1]
    conventions_pos = design_prompt.index("Engineering Skill Conventions: widgetlib")
    reminder_pos = design_prompt.index("apply the Engineering Skill Conventions above when defining this design")
    assert reminder_pos > conventions_pos


@pytest.mark.asyncio
async def test_architect_prompt_no_skill_reminder_without_active_skills(tmp_path):
    cfg = AppConfig()
    cfg.paths.skills = str(tmp_path / "skills")
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(return_value="OK")
    we = WorkflowEngine(kernel, llm)

    await we.run_generation_workflow(goal="Print hello", workspace_path=str(tmp_path))

    design_prompt = llm.complete.call_args_list[1][0][1]
    assert "apply the Engineering Skill Conventions above when defining this design" not in design_prompt
