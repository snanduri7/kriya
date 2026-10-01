"""D5b (KNOW A on demo-runtime-5, 2026-10-01, run 20261001T183220-58ceab2e):
recovery evidence for a stack-frame grounding includes the exception message.

Live: IgniteDemoApp.java:19 called Ignition.ignite() with no node started
(IgniteIllegalStateException: Ignite instance with provided name doesn't
exist). Grounding and owner were correct (s3, [IgniteDemoApp.java]), but D5's
excerpt started at the line first naming the file - the stack frame, below the
message - so the reopened owner saw only frames and regenerated identical
bytes (RECOVERY_NO_PROGRESS). The window now starts at the header of the
exception block the frame belongs to; a resource-named failure is unchanged."""
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from _strict_doubles import developer_double
from test_workflow import _runtime_verifier_ctx

from kriya.policy.filesystem import WriteScopeMode
from kriya.workflow.attempt import run_attempt
from kriya.workflow.failure import QualityGateFailure
from kriya.workflow.failure_grounding import grounded_evidence_excerpt
from kriya.workflow.retry_strategy import handle_attempt_failure
from kriya.workflow.state import GenerationState

FIXTURES = Path(__file__).parent / "fixtures" / "d5_runtime_resource"
APP = "src/main/java/com/example/IgniteDemoApp.java"
CONFIG = "src/main/resources/ignite-config.xml"
TARGET = "src/main/java/t/Target.java"
NOISE = "[WARNING] The POM for org.apache.maven.plugins:maven-jar-plugin:jar:3.5.0 is missing\n" * 40  # > the bound


def test_the_live_stack_frame_failure_shows_its_exception_message():
    excerpt = grounded_evidence_excerpt((FIXTURES / "live_runtime5_s4.txt").read_text(), [APP])
    assert excerpt.startswith("class org.apache.ignite.IgniteIllegalStateException: Ignite instance with provided name")
    for text in ("IgniteIllegalStateException", "Ignite instance with provided name doesn't exist",
                 "IgniteDemoApp.java:19"):
        assert text in excerpt


def test_the_live_resource_failure_is_unchanged():
    excerpt = grounded_evidence_excerpt((FIXTURES / "live_runtime3_s4.txt").read_text(), [CONFIG])
    assert excerpt.startswith("WARNING: Exception encountered during context initialization")
    for text in ("NotWritablePropertyException", "ignite-config.xml", "Invalid property 'gridStartTime'"):
        assert text in excerpt


def test_a_caused_by_block_is_its_own_exception():
    output = (NOISE + "org.outer.OuterException: wrapper\n\tat o.Outer.run(Outer.java:1)\n"
              "Caused by: com.x.InnerException: actual cause\n\tat lib.L.call(L.java:3)\n"
              "\tat t.Target.run(Target.java:42)\n\t... 4 more\n")
    assert grounded_evidence_excerpt(output, [TARGET]).startswith("Caused by: com.x.InnerException: actual cause")


def test_a_frame_deep_in_a_framework_trace_still_reaches_its_header():
    library = "".join(f"\tat org.springframework.beans.Factory.step{n}(Factory.java:{n})\n" for n in range(30))
    output = NOISE + "org.springframework.beans.factory.BeanCreationException: real cause\n" + library + \
        "\tat t.Target.run(Target.java:42)\n"
    excerpt = grounded_evidence_excerpt(output, [TARGET])
    assert excerpt.startswith("org.springframework.beans.factory.BeanCreationException: real cause")
    assert "Target.java:42" in excerpt


def test_an_unrelated_earlier_exception_is_never_chosen():
    output = (NOISE + "java.lang.IllegalStateException: unrelated\n\tat o.Other.x(Other.java:5)\n[INFO] next step\n"
              "foo.BarException: the real failure\n\tat t.Target.run(Target.java:42)\n")
    excerpt = grounded_evidence_excerpt(output, [TARGET])
    assert excerpt.startswith("foo.BarException: the real failure") and "unrelated" not in excerpt


@pytest.mark.parametrize(("header", "expected_start"), [
    ('Exception in thread "main" java.lang.NullPointerException: x is null', 'Exception in thread "main"'),
    ("org.springframework.beans.factory.parsing.BeanDefinitionParsingException: Configuration problem: x\n"
     "Offending resource: class path resource [a.xml]", "org.springframework.beans.factory.parsing"),  # multi-line
    ("java.lang.StackOverflowError", "java.lang.StackOverflowError"),
])
def test_header_shapes(header, expected_start):
    output = NOISE + header + "\n\tat lib.L.call(L.java:3)\n\tat t.Target.run(Target.java:42)\n"
    assert grounded_evidence_excerpt(output, [TARGET]).startswith(expected_start)


@pytest.mark.parametrize("above", [
    "an ordinary log line",  # no recognizable header: the frame line, as before
    "foo.BarException: too far\n" + "message line\n" * 6,  # beyond the message reach
])
def test_no_header_within_reach_keeps_the_frame_line(above):
    output = NOISE + above + "\n\tat t.Target.run(Target.java:42)\n"
    assert grounded_evidence_excerpt(output, [TARGET]).startswith("\tat t.Target.run(Target.java:42)")


def test_the_excerpt_stays_within_its_bound():
    output = NOISE + "foo.BarException: " + "y" * 5000 + "\n\tat t.Target.run(Target.java:42)\n"
    excerpt = grounded_evidence_excerpt(output, [TARGET])
    assert len(excerpt) == 2000 and excerpt.startswith("foo.BarException: yyy")


@pytest.mark.asyncio
async def test_owner_and_scope_are_unchanged_and_the_owner_sees_the_message(tmp_path):
    """The live shape through run_attempt and handle_attempt_failure: the scope
    conflict names exactly the grounded app file, the verification unit's own
    scope stays empty, and the evidence now carries the message."""
    developer = developer_double()
    developer.run_generation = AsyncMock(side_effect=AssertionError("verification-only: no Developer"))
    run_verifier = AsyncMock()
    run_verifier.judge = AsyncMock(return_value={
        "should_run": True, "run_commands": [["mvn", "-e", "exec:java", "-Dexec.mainClass=com.example.IgniteDemoApp"]],
        "command_source": "inferred", "success_criteria": "prints the cached value"})
    run_verifier.grade = AsyncMock(return_value={"passed": False, "reasoning": "FAIL printed", "likely_files": []})
    ctx = _runtime_verifier_ctx(tmp_path, developer=developer, run_verifier=run_verifier,
                                established_files=["pom.xml", CONFIG, APP])
    state = GenerationState()
    live = {"success": False, "timed_out": False, "returncode": 0, "runtime_prerequisite": "PASSED",
            "output": (FIXTURES / "live_runtime5_s4.txt").read_text(),
            "steps": [{"command": ["mvn"], "exit_code": 0, "timed_out": False}]}
    with patch("kriya.tools.validate.PolymorphicValidator.run_app_sequence", return_value=live):
        with pytest.raises(QualityGateFailure) as raised:
            await run_attempt(state, ctx)
    with patch("kriya.workflow.attribution._tier_triage", new=AsyncMock(side_effect=AssertionError("no model"))):
        assert await handle_attempt_failure(state, ctx, raised.value) is True
    conflict = state.plan_scope_conflict
    assert (conflict["required_files"], conflict["allowed_files"], conflict["attribution_tier"]) == ([APP], [], "locator")
    assert "Ignite instance with provided name doesn't exist" in conflict["raw_evidence"]
    assert ctx.write_scope_mode is WriteScopeMode.DENY_ALL and list(ctx.allowed_write_relpaths) == []
