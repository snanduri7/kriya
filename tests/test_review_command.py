import subprocess
from unittest.mock import AsyncMock, patch

from click.testing import CliRunner

from kriya.cli import main

_A1_JAVA_SOURCE = (
    "public class Target implements TargetInterface {\n"
    "    public Target(Collaborator c) {\n"
    "    }\n"
    "\n"
    "    public void doWork() {\n"
    "    }\n"
    "}\n"
)

# A1-E2: `kriya review`'s single-Java-file path now calls ReviewerAgent.
# run_structured_review() (json_mode=True), so every test that exercises that
# path needs a valid structured JSON response instead of the free-form
# "Looks fine." string the pre-A1-E2 tests used - an unparseable response now
# fails the command clearly by design (see test_review_single_java_file_
# malformed_structured_response_fails_clearly_below).
_STRUCTURED_OK_RESPONSE = (
    '{"summary": "Looks fine.", "member_reviews": [], "findings": [], '
    '"recommendations": [], "run_guidance": {"statements": [], "not_determinable": []}}'
)


def _init_git_repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)


def test_review_command_uses_configured_reviewer_model(tmp_path):
    """Regression test for a real bug found via code review: the standalone
    `kriya review` command built its own ReviewerAgent with no role override at
    all, silently ignoring agent_llms.reviewer - unlike generate's internal
    reviewer stage (workflow.py), which correctly threads it through. A project
    that configures a specific reviewer model got inconsistent behavior between
    `generate`'s embedded review and standalone `review`."""
    (tmp_path / "kriya.yaml").write_text(
        "agent_llms:\n  reviewer:\n    llm:\n      model: devstral-small-2:24b\n"
    )
    (tmp_path / "app.py").write_text("def add(a, b):\n    return a + b\n")

    mock_complete = AsyncMock(return_value="Looks fine.")
    runner = CliRunner()

    with patch("kriya.core.llm.LLMClient.complete", new=mock_complete):
        res = runner.invoke(main, ["--config", str(tmp_path / "kriya.yaml"), "review", str(tmp_path / "app.py")])

    assert res.exit_code == 0, res.output
    mock_complete.assert_called_once()
    assert mock_complete.call_args.kwargs.get("model_override") == "devstral-small-2:24b"


def test_review_directory_includes_ruby_files(tmp_path):
    """Regression test for a real bug found via code review: directory review's
    extension allowlist was {".py", ".java", ".xml"} - missing ".rb", even
    though PolymorphicValidator elsewhere explicitly supports Ruby. Ruby files
    in a directory review were silently skipped entirely."""
    (tmp_path / "app.py").write_text("print('hi')\n")
    (tmp_path / "app.rb").write_text("puts 'hi'\n")

    mock_complete = AsyncMock(return_value="Looks fine.")
    runner = CliRunner()

    with patch("kriya.core.llm.LLMClient.complete", new=mock_complete):
        res = runner.invoke(main, ["review", str(tmp_path)])

    assert res.exit_code == 0, res.output
    prompt = mock_complete.call_args[0][1]
    assert "app.py" in prompt
    assert "app.rb" in prompt


def test_review_directory_git_status_handles_filename_with_space(tmp_path):
    """Regression test for a real bug found via code review: the old
    `line.strip().split()` + `parts[-1]` parsing of `git status --porcelain`
    output splits a space-containing filename into fragments, so `parts[-1]`
    resolves to a nonexistent file and the git-status path silently finds
    NOTHING - which then falls through to the (correct-by-accident) recursive
    fallback scan, masking the bug rather than exposing it. Distinguishes the
    two code paths directly: `unrelated.py` is committed with no git changes,
    so it must NEVER appear if the git-status path is genuinely the one that
    found the file - only the (unwanted, bug-masking) fallback scan would
    include it. Uses -z (NUL-separated, unquoted) instead of the ambiguous
    default porcelain format."""
    _init_git_repo(tmp_path)
    (tmp_path / "unrelated.py").write_text("# committed, never modified\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=tmp_path, check=True)

    spaced = tmp_path / "my task file.py"
    spaced.write_text("def run():\n    pass\n")

    mock_complete = AsyncMock(return_value="Looks fine.")
    runner = CliRunner()

    with patch("kriya.core.llm.LLMClient.complete", new=mock_complete):
        res = runner.invoke(main, ["review", str(tmp_path)])

    assert res.exit_code == 0, res.output
    prompt = mock_complete.call_args[0][1]
    assert "my task file.py" in prompt
    assert "unrelated.py" not in prompt


def test_review_directory_git_status_handles_rename_with_space(tmp_path):
    """The -z porcelain format represents a rename as two separate
    NUL-terminated entries (status+old_path, then a bare new_path with no
    status prefix) - must correctly consume the destination path as the
    file to review, not the source, and not misparse a bare continuation
    entry as a fresh status-prefixed one."""
    _init_git_repo(tmp_path)
    original = tmp_path / "old_name.py"
    original.write_text("def run():\n    pass\n")
    (tmp_path / "unrelated.py").write_text("# committed, never modified\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=tmp_path, check=True)

    renamed = tmp_path / "new name with space.py"
    subprocess.run(["git", "mv", "old_name.py", "new name with space.py"], cwd=tmp_path, check=True)
    assert renamed.exists()

    mock_complete = AsyncMock(return_value="Looks fine.")
    runner = CliRunner()

    with patch("kriya.core.llm.LLMClient.complete", new=mock_complete):
        res = runner.invoke(main, ["review", str(tmp_path)])

    assert res.exit_code == 0, res.output
    prompt = mock_complete.call_args[0][1]
    assert "new name with space.py" in prompt
    # Discriminates the correct git-status-driven path from the fallback
    # recursive scan (which would also happen to find the renamed file by
    # extension, masking a parsing bug the same way it did before this test
    # was strengthened) - unrelated.py has no git changes and must never
    # appear if the git-status path is the one actually doing the work.
    assert "unrelated.py" not in prompt


def test_review_directory_warns_when_truncated_at_ten_files(tmp_path):
    for i in range(12):
        (tmp_path / f"mod_{i}.py").write_text(f"x = {i}\n")

    mock_complete = AsyncMock(return_value="Looks fine.")
    runner = CliRunner()

    with patch("kriya.core.llm.LLMClient.complete", new=mock_complete):
        res = runner.invoke(main, ["review", str(tmp_path)])

    assert res.exit_code == 0, res.output
    assert "only the first 10" in res.output


def test_review_directory_no_warning_under_ten_files(tmp_path):
    for i in range(3):
        (tmp_path / f"mod_{i}.py").write_text(f"x = {i}\n")

    mock_complete = AsyncMock(return_value="Looks fine.")
    runner = CliRunner()

    with patch("kriya.core.llm.LLMClient.complete", new=mock_complete):
        res = runner.invoke(main, ["review", str(tmp_path)])

    assert res.exit_code == 0, res.output
    assert "only the first 10" not in res.output


def test_review_single_file_exceeding_budget_is_truncated_with_warning(tmp_path):
    """Regression test for a real, severe bug found live: with no size control at
    all, a file exceeding the model's context window got silently truncated from
    the FRONT by the backend, cutting off every "=== File: ... ===" framing
    marker along with it - the model received an unlabeled code fragment with no
    idea it was even being asked to review anything, produced a confused
    non-review response, and Kriya still reported success with no warning
    whatsoever. Must now: warn the user, and mark the truncation explicitly in
    what's sent to the model too, rather than truncate invisibly."""
    (tmp_path / "kriya.yaml").write_text("llm:\n  context_window: 500\n")
    # ~800 words - estimate_tokens (~1.3x word count) puts this well over the
    # budget (500 * 0.75 = 375 tokens).
    big_content = "\n".join(f"x_{i} = {i}  # padding line number {i}" for i in range(200))
    (tmp_path / "big.py").write_text(big_content)

    mock_complete = AsyncMock(return_value="Looks fine.")
    runner = CliRunner()

    with patch("kriya.core.llm.LLMClient.complete", new=mock_complete):
        res = runner.invoke(main, ["--config", str(tmp_path / "kriya.yaml"), "review", str(tmp_path / "big.py")])

    assert res.exit_code == 0, res.output
    assert "too large to review in full" in res.output
    prompt_sent_to_model = mock_complete.call_args[0][1]
    assert "TRUNCATED" in prompt_sent_to_model
    assert "x_199" not in prompt_sent_to_model  # the tail never made it in


def test_review_multiple_files_over_budget_splits_into_batches(tmp_path):
    """When several files' combined content doesn't fit one call, must degrade
    to multiple separate review calls (each within budget) rather than either
    silently truncating the combined prompt or crashing - every file must
    actually reach the model in some call, clearly labeled which batch."""
    (tmp_path / "kriya.yaml").write_text("llm:\n  context_window: 310\n")
    # ~30 lines, one file's wrapped review blob estimates to ~157 tokens -
    # comfortably under the 232-token budget (0.75 * 310) alone, but two of
    # them combined (~314) exceed it.
    padding = "\n".join(f"x_{i} = {i}  # padding" for i in range(30))
    (tmp_path / "a.py").write_text(padding)
    (tmp_path / "b.py").write_text(padding.replace("x_", "y_"))

    mock_complete = AsyncMock(return_value="Looks fine.")
    runner = CliRunner()

    with patch("kriya.core.llm.LLMClient.complete", new=mock_complete):
        res = runner.invoke(main, ["--config", str(tmp_path / "kriya.yaml"), "review", str(tmp_path)])

    assert res.exit_code == 0, res.output
    assert mock_complete.await_count == 2
    assert "batch 1/2" in res.output
    assert "batch 2/2" in res.output
    prompt_1 = mock_complete.await_args_list[0][0][1]
    prompt_2 = mock_complete.await_args_list[1][0][1]
    assert "a.py" in prompt_1 and "b.py" not in prompt_1
    assert "b.py" in prompt_2 and "a.py" not in prompt_2


def test_review_small_files_still_use_a_single_combined_call(tmp_path):
    # The common case (content comfortably fits) must be unchanged: one
    # combined call with full cross-file context, not needlessly split.
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "b.py").write_text("y = 2\n")

    mock_complete = AsyncMock(return_value="Looks fine.")
    runner = CliRunner()

    with patch("kriya.core.llm.LLMClient.complete", new=mock_complete):
        res = runner.invoke(main, ["review", str(tmp_path)])

    assert res.exit_code == 0, res.output
    assert mock_complete.await_count == 1
    prompt = mock_complete.await_args_list[0][0][1]
    assert "a.py" in prompt and "b.py" in prompt


def test_review_prompt_frames_absence_of_a_goal(tmp_path):
    """Stage 6 SME review, Finding 3 (2026-08-15): unlike the embedded pipeline's
    Reviewer stage, which always prefixes "Goal: {goal}...", the standalone command
    sent bare file blobs with no framing - a real mismatch with ReviewerAgent's own
    system prompt, which assumes a goal exists to calibrate its "don't reject for
    missing tests" leniency against. Must now tell the model explicitly there's no
    goal, rather than leaving it to guess or invent requirements."""
    (tmp_path / "app.py").write_text("def add(a, b):\n    return a + b\n")

    mock_complete = AsyncMock(return_value="Looks fine.")
    runner = CliRunner()

    with patch("kriya.core.llm.LLMClient.complete", new=mock_complete):
        res = runner.invoke(main, ["review", str(tmp_path / "app.py")])

    assert res.exit_code == 0, res.output
    prompt = mock_complete.call_args[0][1]
    assert "No specific goal or task was provided" in prompt
    assert "Do not invent or assume requirements" in prompt
    assert "app.py" in prompt


def test_review_stdout_contains_only_the_review_text(tmp_path):
    """Regression test for a real bug found while auditing other commands for
    the same stdout-pollution shape as `prompt generate`: "Scanning
    directory: ...", "Reviewing N file(s)...", and the "=== Code Review
    Report ===" batch header were all plain click.secho() with no err=True,
    mixed into stdout alongside the reviewer's actual streamed output.
    Verified live before fixing. Chrome now goes to stderr; stdout must
    contain ONLY the streamed review text."""
    (tmp_path / "app.py").write_text("def add(a, b):\n    return a + b\n")

    async def fake_complete(*args, stream_callback=None, **kwargs):
        if stream_callback:
            stream_callback("This looks correct.")
        return "This looks correct."

    runner = CliRunner()
    with patch("kriya.core.llm.LLMClient.complete", new=AsyncMock(side_effect=fake_complete)):
        res = runner.invoke(main, ["review", str(tmp_path / "app.py")])

    assert res.exit_code == 0, res.output
    assert res.stdout.strip() == "This looks correct."
    assert "Scanning directory" not in res.stdout
    assert "Reviewing" not in res.stdout
    assert "Code Review Report" not in res.stdout
    assert "Reviewing 1 file(s)" in res.stderr
    assert "Code Review Report" in res.stderr


def test_review_single_java_file_includes_repository_aware_contract_sections(tmp_path):
    """A1-P1/A1-E2: reviewing exactly one .java file must assemble the four
    labeled sections (TARGET SOURCE / Deterministic Symbol Inventory /
    Repository Evidence / REVIEW TASK) deterministically, BEFORE the model
    call - the model must not be expected to rediscover the member
    inventory or repository relationships itself. Each member/relation now
    also carries a Kriya-generated evidence id (M#/R#)."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "Target.java").write_text(_A1_JAVA_SOURCE)
    (tmp_path / "TargetInterface.java").write_text("public interface TargetInterface {\n}\n")
    (tmp_path / "Collaborator.java").write_text("public class Collaborator {\n}\n")
    (tmp_path / "TargetTest.java").write_text("public class TargetTest {\n}\n")

    mock_complete = AsyncMock(return_value=_STRUCTURED_OK_RESPONSE)
    runner = CliRunner()

    with patch("kriya.core.llm.LLMClient.complete", new=mock_complete):
        res = runner.invoke(main, ["review", str(tmp_path / "Target.java")])

    assert res.exit_code == 0, res.output
    prompt = mock_complete.call_args[0][1]
    assert "=== TARGET SOURCE ===" in prompt
    assert "Deterministic Symbol Inventory" in prompt
    assert "M1" in prompt
    assert "Target(Collaborator)" in prompt
    assert "doWork()" in prompt
    assert "Repository Evidence" in prompt
    assert "R1" in prompt
    assert "TargetInterface.java" in prompt
    assert "Collaborator.java" in prompt
    assert "TargetTest.java" in prompt
    assert "=== REVIEW TASK ===" in prompt
    assert mock_complete.call_args.kwargs.get("json_mode") is True


def test_review_non_java_single_file_has_no_java_contract_sections(tmp_path):
    """The repository-aware contract is Java-only (A1 scope) - a Python file
    must go through the exact same path as before, with no member-inventory
    or repository-evidence sections attached."""
    (tmp_path / "app.py").write_text("def add(a, b):\n    return a + b\n")

    mock_complete = AsyncMock(return_value="Looks fine.")
    runner = CliRunner()

    with patch("kriya.core.llm.LLMClient.complete", new=mock_complete):
        res = runner.invoke(main, ["review", str(tmp_path / "app.py")])

    assert res.exit_code == 0, res.output
    prompt = mock_complete.call_args[0][1]
    assert "Deterministic Symbol Inventory" not in prompt
    assert "Repository Evidence" not in prompt


def test_review_single_java_file_related_artifact_content_never_leaks_into_prompt(tmp_path):
    """A1-R1 regression fixture (2026-09-09): reproduces the exact shape of
    the first live A1 run's evidence-leakage failure - a target method that
    self-invokes another @Transactional method in the same class (DEFECT 2's
    pattern), plus a related, constructor-injected collaborator file (DEFECT
    1's DriverController shape: a real repository-context relation, its
    CONTENT never supplied - the same "caller/collaborator named, contents
    withheld" shape, using the collaborator relation since it is the
    deterministic relation type this synthetic two-file fixture reliably
    produces). Asserts the constructed prompt/context contains enough
    evidence-boundary information to prohibit both observed failure modes -
    not that a model will obey it (that's what the next live run is for),
    but that Kriya itself never leaks the collaborator's content and that
    the contract text reaches the actual assembled prompt used by the
    review command. Per explicit instruction: do NOT fix this by supplying
    every related file's content - the fix must be Reviewer-contract-only,
    and this test would fail that instruction if it ever found
    CALLER_BODY_MARKER_TOKEN leaking into the prompt via a repository-
    context expansion instead."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "Target.java").write_text(
        "public class Target {\n"
        "    private final Caller caller;\n"
        "\n"
        "    public Target(Caller caller) {\n"
        "        this.caller = caller;\n"
        "    }\n"
        "\n"
        "    @Transactional\n"
        "    public void outer() {\n"
        "        inner();\n"
        "    }\n"
        "\n"
        "    @Transactional\n"
        "    public void inner() {\n"
        "    }\n"
        "}\n"
    )
    (tmp_path / "Caller.java").write_text(
        "public class Caller {\n"
        "    public void run() {\n"
        "        // CALLER_BODY_MARKER_TOKEN - must never reach the review prompt\n"
        "    }\n"
        "}\n"
    )

    mock_complete = AsyncMock(return_value=_STRUCTURED_OK_RESPONSE)
    runner = CliRunner()

    with patch("kriya.core.llm.LLMClient.complete", new=mock_complete):
        res = runner.invoke(main, ["review", str(tmp_path / "Target.java")])

    assert res.exit_code == 0, res.output
    system_prompt = mock_complete.call_args[0][0]
    user_prompt = mock_complete.call_args[0][1]

    # DEFECT 1 regression: Caller.java is correctly identified as a related
    # artifact (by name/relation only) but its actual body is never supplied
    # anywhere in what's actually sent to the model (system OR user prompt).
    assert "Caller.java" in user_prompt
    assert "CALLER_BODY_MARKER_TOKEN" not in system_prompt and "CALLER_BODY_MARKER_TOKEN" not in user_prompt
    assert "public void run()" not in system_prompt and "public void run()" not in user_prompt

    # A1-E2: this path now uses the STRUCTURED system prompt (not the
    # free-form one) - the boundary rule and requested-vs-final-confidence
    # framing must reach the real system prompt used for this exact call.
    assert "never that related file's unseen contents" in system_prompt
    assert "Never invent an id" in system_prompt
    assert "advisory only" in system_prompt

    # The self-invocation pattern itself is still visible in the target
    # source, as it always was - only the CONFIDENCE contract changed.
    assert "inner();" in user_prompt


def test_review_command_never_touches_write_or_generation_machinery(tmp_path):
    """Zero-write regression test: `kriya review` (including the new A1-P1
    single-Java-file repository-context path) must never construct
    AuthorizedFileWriter, never construct DeveloperAgent, and never call
    run_generation_workflow - the read-only review path has no legitimate
    reason to reach any of them. Patched to raise if touched, rather than
    merely asserting call counts, so this fails loudly on any new code path
    that reaches them, not just the ones anticipated today."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "Target.java").write_text(_A1_JAVA_SOURCE)
    (tmp_path / "TargetInterface.java").write_text("public interface TargetInterface {\n}\n")

    mock_complete = AsyncMock(return_value=_STRUCTURED_OK_RESPONSE)
    runner = CliRunner()

    def _boom(*args, **kwargs):
        raise AssertionError("review command must never construct/call write or generation machinery")

    with patch("kriya.core.llm.LLMClient.complete", new=mock_complete), \
         patch("kriya.policy.filesystem.AuthorizedFileWriter.__init__", side_effect=_boom), \
         patch("kriya.agents.agent.DeveloperAgent.__init__", side_effect=_boom), \
         patch("kriya.workflow.workflow.WorkflowEngine.run_generation_workflow", side_effect=_boom):
        res = runner.invoke(main, ["review", str(tmp_path / "Target.java")])

    assert res.exit_code == 0, res.output


def test_review_single_java_file_raw_json_never_streamed_or_printed(tmp_path):
    """A1-E2 explicit requirement: raw structured JSON must never reach the
    user - stdout must contain the rendered Markdown report, never the raw
    JSON keys, and the model call itself must not be given a stream
    callback (nothing to stream meaningfully mid-generation for a
    single-shot JSON response)."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "Target.java").write_text(_A1_JAVA_SOURCE)

    mock_complete = AsyncMock(return_value=_STRUCTURED_OK_RESPONSE)
    runner = CliRunner()

    with patch("kriya.core.llm.LLMClient.complete", new=mock_complete):
        res = runner.invoke(main, ["review", str(tmp_path / "Target.java")])

    assert res.exit_code == 0, res.output
    assert '"summary"' not in res.stdout
    assert '"findings"' not in res.stdout
    assert "### Findings" in res.stdout
    assert "## How to Run the Application" in res.stdout
    assert mock_complete.call_args.kwargs.get("stream_callback") is None


def test_review_single_java_file_malformed_structured_response_fails_clearly(tmp_path):
    """A1-E2 explicit requirement: a malformed structured response must not
    silently fall back to unvalidated free-form Markdown - the command must
    exit non-zero with a clear error, not print anything that looks like a
    review."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "Target.java").write_text(_A1_JAVA_SOURCE)

    mock_complete = AsyncMock(return_value="not JSON at all")
    runner = CliRunner()

    with patch("kriya.core.llm.LLMClient.complete", new=mock_complete):
        res = runner.invoke(main, ["review", str(tmp_path / "Target.java")])

    assert res.exit_code != 0
    assert "Structured review failed" in res.stderr
    assert "### Findings" not in res.stdout


def test_review_single_java_file_reports_confidence_downgrade_end_to_end(tmp_path):
    """End-to-end proof (no live model) that a Reviewer-requested PROVEN_ISSUE
    with real condition evidence but no consequence evidence is rendered to
    the user as a Kriya-downgraded STRONG STATIC INDICATION, exactly the
    shape both historical A1 over-classifications had."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "Target.java").write_text(_A1_JAVA_SOURCE)

    raw = (
        '{"summary": "ok", "member_reviews": [{"member_id": "M1", "status": "finding"}], '
        '"findings": [{"finding_id": "F1", "title": "Overclaimed issue", "member_id": "M1", '
        '"requested_confidence": "PROVEN_ISSUE", "condition_evidence_ids": ["M1"], '
        '"consequence_evidence_ids": [], "runtime_dependency_declared": false, '
        '"explanation": "a pattern is present"}], "recommendations": [], '
        '"run_guidance": {"statements": [], "not_determinable": []}}'
    )
    mock_complete = AsyncMock(return_value=raw)
    runner = CliRunner()

    with patch("kriya.core.llm.LLMClient.complete", new=mock_complete):
        res = runner.invoke(main, ["review", str(tmp_path / "Target.java")])

    assert res.exit_code == 0, res.output
    assert "STRONG STATIC INDICATION" in res.stdout
    assert "Reviewer requested: PROVEN ISSUE" in res.stdout
    assert "Kriya downgraded" in res.stdout


def test_review_single_java_file_member_coverage_rendered(tmp_path):
    """Member coverage (missing/invented ids) must be explicit in the
    rendered report, via exact id-set comparison against the deterministic
    inventory - not name-presence text scanning."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "Target.java").write_text(_A1_JAVA_SOURCE)

    raw = (
        '{"summary": "ok", "member_reviews": [{"member_id": "M1", "status": "no_issue"}], '
        '"findings": [], "recommendations": [], '
        '"run_guidance": {"statements": [], "not_determinable": []}}'
    )
    mock_complete = AsyncMock(return_value=raw)
    runner = CliRunner()

    with patch("kriya.core.llm.LLMClient.complete", new=mock_complete):
        res = runner.invoke(main, ["review", str(tmp_path / "Target.java")])

    assert res.exit_code == 0, res.output
    assert "1/2 deterministic member(s) accounted for" in res.stdout
    assert "M2" in res.stdout  # the missing member (doWork()) is called out by id
