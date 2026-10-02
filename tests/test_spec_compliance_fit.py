"""Live matrix, commons-lang strip-accents (run 7d3b2550): the spec-compliance
request carried every written file whole. StringUtils.java (~9,000 lines) made
it ~189,521 prompt tokens against a 32,768 window, so the request was refused
before inference and the authoritative path (an unavailable judgment is never
"satisfied") stopped a candidate that had compiled and passed its tests. Any
task editing a large file could never succeed.

The files section is now fitted to the request: every file whole when that
fits (the prompt is unchanged), else each file's changed regions against its
pre-change version, with the widest context that fits.
"""
import asyncio
from unittest.mock import MagicMock, patch

from kriya.agents.agent import SpecComplianceAgent
from kriya.config import AppConfig
from kriya.workflow.context_budget import SPEC_CHANGED_REGIONS_NOTE, agent_request_capacity

GOAL = "strip accents from the letters d-stroke"
CHANGED = '        return stripped.replace("\\u0111", "d");'
BIG_BEFORE = "public class StringUtils {\n" + "".join(
    f"    public static String helper{i}(String s) {{\n        return s + \"{i}\";\n    }}\n\n" for i in range(4000)
) + "    public static String stripAccents(String input) {\n        return stripped;\n    }\n}\n"
BIG_AFTER = BIG_BEFORE.replace("        return stripped;\n", CHANGED + "\n")


def _check(contents, baselines=None):
    config = AppConfig()
    config.llm.context_window = 32768
    agent = SpecComplianceAgent("spec_compliance", MagicMock(config=config))
    sent = []

    async def escalation(llm, system, prompt, *args, **kwargs):
        sent.append((system, prompt))
        return '{"compliant": true, "reasoning": "ok", "missing_requirements": []}'

    with patch("kriya.agents.agent.call_with_escalation", new=escalation):
        asyncio.run(agent.check(GOAL, sorted(contents), contents, baseline_contents=baselines))
    [(system, prompt)] = sent
    return agent_request_capacity(config, agent, "spec_compliance"), system, prompt


def test_a_file_too_large_to_show_whole_is_judged_on_its_changed_regions():
    capacity, system, prompt = _check({"StringUtils.java": BIG_AFTER}, {"StringUtils.java": BIG_BEFORE})
    assert capacity.count(system) + capacity.count(prompt) <= capacity.tokens  # the request fits: it is sent
    assert SPEC_CHANGED_REGIONS_NOTE in prompt and "(changed regions only)" in prompt
    assert "+" + CHANGED in prompt and "-        return stripped;" in prompt
    assert "helper10(" not in prompt  # unchanged code is not shown


def test_files_that_fit_are_shown_whole_exactly_as_before():
    small = {"Greeter.java": "class Greeter { String hi() { return \"hi\"; } }\n"}
    _, _, prompt = _check(small, {"Greeter.java": "class Greeter {}\n"})
    assert prompt == (f"=== Goal ===\n{GOAL}\n\n=== Files Generated ===\n=== Greeter.java ===\n"
                      f"{small['Greeter.java']}\n\nDoes this code satisfy every concrete, literally-named "
                      "requirement in the goal, per the rules above?")


def test_without_a_pre_change_version_nothing_is_invented():
    """No baseline: the whole file is kept - the dispatch check then refuses
    the request typed (an unavailable judgment), never a verdict over a
    view Kriya could not build."""
    capacity, system, prompt = _check({"StringUtils.java": BIG_AFTER})
    assert BIG_AFTER in prompt and capacity.count(system) + capacity.count(prompt) > capacity.tokens


def test_a_new_file_is_shown_whole_beside_the_changed_regions_of_another():
    new = "class Accents { static String map(char c) { return \"d\"; } }\n"
    _, _, prompt = _check({"StringUtils.java": BIG_AFTER, "Accents.java": new},
                          {"StringUtils.java": BIG_BEFORE, "Accents.java": None})
    assert f"=== Accents.java ===\n{new}" in prompt and "+" + CHANGED in prompt


def test_an_upstream_file_this_candidate_did_not_change_is_named_not_shown():
    upstream = BIG_BEFORE.replace("StringUtils", "CharUtils")
    _, _, prompt = _check({"StringUtils.java": BIG_AFTER, "CharUtils.java": upstream},
                          {"StringUtils.java": BIG_BEFORE, "CharUtils.java": upstream})
    assert "=== CharUtils.java (unchanged by this candidate; not shown) ===" in prompt
    assert "class CharUtils" not in prompt and "+" + CHANGED in prompt
