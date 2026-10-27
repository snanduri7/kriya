"""Tests for kriya/tools/lsp.py: the PATH lookup and the diagnostics prompt formatter.

S8-ORPHAN-HOST-CODE-001: the jdtls process machinery (stdio framing, reader loop, requests, the private candidate mirror, tree-kill shutdown) was unreachable - JdtlsClient.start() is
always the typed refusal because containment is mandatory and jdtls has no contained mode - and is deleted with its tests. What this module now pins is what survives; the refusal and the
absence of the machinery are pinned in tests/test_s7_secure_default_contract.py and tests/test_s8_host_branches_are_gone.py."""
from unittest.mock import patch

from kriya.tools.lsp import (
    find_jdtls,
    format_diagnostics_for_prompt,
)


def test_find_jdtls_returns_none_when_not_on_path():
    with patch("shutil.which", return_value=None):
        assert find_jdtls() is None


def test_find_jdtls_returns_path_when_found():
    with patch("shutil.which", return_value="/opt/homebrew/bin/jdtls"):
        assert find_jdtls() == "/opt/homebrew/bin/jdtls"


def test_format_diagnostics_for_prompt_filters_to_errors_only():
    diagnostics = [
        {"severity": 1, "range": {"start": {"line": 4}}, "message": "cannot resolve import"},
        {"severity": 2, "range": {"start": {"line": 10}}, "message": "unused variable"},
    ]
    result = format_diagnostics_for_prompt("IntegrationApp.java", diagnostics)
    assert "cannot resolve import" in result
    assert "unused variable" not in result
    assert "[line 5]" in result  # 0-indexed LSP line -> 1-indexed for humans

def test_format_diagnostics_for_prompt_empty_when_no_errors():
    diagnostics = [{"severity": 2, "range": {"start": {"line": 0}}, "message": "unused import"}]
    assert format_diagnostics_for_prompt("App.java", diagnostics) == ""

def test_format_diagnostics_for_prompt_empty_on_no_diagnostics():
    assert format_diagnostics_for_prompt("App.java", []) == ""

def test_format_diagnostics_for_prompt_is_forceful_ground_truth_framing():
    # The user explicitly asked for this to compel a fix, not read as a
    # passive suggestion - confirm the framing is actually present, not just
    # the raw diagnostic text.
    diagnostics = [{"severity": 1, "range": {"start": {"line": 0}}, "message": "cannot resolve import IgniteCache"}]
    result = format_diagnostics_for_prompt("App.java", diagnostics)
    assert "ground truth" in result.lower()
    assert "not a guess" in result.lower()
    assert "same error WILL happen again" in result
