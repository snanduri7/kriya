"""Deterministic regression coverage for the three bounded residual fixes
in this work package (R4 - the Django classification test - was
investigated but left unmodified; see the session's own report, no
production change was justified there):

- R1 (PERF/DEPENDENCY-001): PolymorphicValidator._ensure_project_venv no
  longer re-runs `pip install` on every call within one instance's
  lifetime when the underlying dependency manifest's real content hasn't
  changed - kriya/tools/validate.py.
- R2 (SOURCE-2): _resolve_retry_member_hints' own SOURCE-3 (search-
  evidence) grounding gate no longer treats "no recorded known_target_
  context_items entry at all" the same as "shown in full" - an absent
  entry is evidence of nothing, not of exactness, so it must not block
  grounding an anchored_edit failure's SEARCH text - kriya/workflow/
  attempt.py.
- R3: PlannerAgent now honors llm.planner_max_tokens (BaseAgent.
  max_output_tokens, set once at WorkflowEngine construction) instead of
  silently falling through to the general llm.max_tokens default -
  kriya/workflow/workflow.py.
"""

import hashlib
import os
from unittest.mock import AsyncMock, patch

import pytest

from kriya.config import AppConfig
from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.tools.validate import PolymorphicValidator
from kriya.workflow.attempt import _resolve_retry_member_hints
from kriya.workflow.context_package import make_context_item
from kriya.workflow.context_source import SourceDerivationCache
from kriya.workflow.failure import Failure, FileLocation
from kriya.workflow.state import GenerationState
from kriya.workflow.workflow import WorkflowEngine

# ---------------------------------------------------------------------------
# R1 - PERF/DEPENDENCY-001: duplicate dependency-acquisition calls
# ---------------------------------------------------------------------------

def _make_fake_venv(workspace_path: str) -> None:
    """Pre-creates the exact on-disk shape _ensure_project_venv_impl checks
    for ('.kriya/venv/bin/python' already existing) so these tests exercise
    only the pip-install caching this residual is about, never the separate
    (already-idempotent, on-disk-checked) venv-creation step."""
    venv_bin = os.path.join(workspace_path, ".kriya", "venv", "bin")
    os.makedirs(venv_bin, exist_ok=True)
    python_path = os.path.join(venv_bin, "python")
    with open(python_path, "w", encoding="utf-8") as fh:
        fh.write("#!/bin/sh\n")
    os.chmod(python_path, 0o755)


class TestPerfDependency001VenvCache:
    def test_repeated_interpreter_resolution_acquires_once(self, tmp_path):
        _make_fake_venv(str(tmp_path))
        (tmp_path / "requirements.txt").write_text("requests==2.31.0\n", encoding="utf-8")
        validator = PolymorphicValidator(str(tmp_path))
        with patch.object(
            validator, "_run_cmd_with_timeout",
            return_value={"returncode": 0, "stdout": "", "stderr": ""},
        ) as mock_run:
            first = validator._resolve_python_interpreter()
            second = validator._resolve_python_interpreter()
            third = validator._resolve_python_interpreter()
        assert first == second == third
        assert validator.venv_install_attempts == 1
        assert mock_run.call_count == 1

    def test_multiple_validator_methods_share_one_acquisition(self, tmp_path):
        # Mirrors the real shape: ONE validator instance, compile_check then
        # run_tests then run_app_sequence all resolving the SAME interpreter.
        _make_fake_venv(str(tmp_path))
        (tmp_path / "requirements.txt").write_text("django==5.0\n", encoding="utf-8")
        validator = PolymorphicValidator(str(tmp_path))
        with patch.object(
            validator, "_run_cmd_with_timeout",
            return_value={"returncode": 0, "stdout": "", "stderr": ""},
        ):
            for _ in range(4):
                validator._resolve_python_interpreter()
        assert validator.venv_install_attempts == 1

    def test_manifest_content_change_invalidates_cache(self, tmp_path):
        _make_fake_venv(str(tmp_path))
        req_path = tmp_path / "requirements.txt"
        req_path.write_text("requests==2.31.0\n", encoding="utf-8")
        validator = PolymorphicValidator(str(tmp_path))
        with patch.object(
            validator, "_run_cmd_with_timeout",
            return_value={"returncode": 0, "stdout": "", "stderr": ""},
        ) as mock_run:
            validator._resolve_python_interpreter()
            assert mock_run.call_count == 1
            req_path.write_text("requests==2.31.0\nflask==3.0\n", encoding="utf-8")
            validator._resolve_python_interpreter()
            assert mock_run.call_count == 2
            # Unchanged again after the edit - back to a cache hit.
            validator._resolve_python_interpreter()
            assert mock_run.call_count == 2
        assert validator.venv_install_attempts == 2

    def test_failure_is_cached_verbatim_never_reinterpreted_as_success(self, tmp_path):
        _make_fake_venv(str(tmp_path))
        (tmp_path / "requirements.txt").write_text("nonexistent-package==0.0.0\n", encoding="utf-8")
        validator = PolymorphicValidator(str(tmp_path))
        with patch.object(
            validator, "_run_cmd_with_timeout",
            return_value={"returncode": 1, "stdout": "", "stderr": "No matching distribution found"},
        ) as mock_run:
            first_interpreter, first_error = validator._resolve_python_interpreter()
            second_interpreter, second_error = validator._resolve_python_interpreter()
        assert first_error is not None and "No matching distribution found" in first_error
        assert first_interpreter == second_interpreter
        assert first_error == second_error
        assert mock_run.call_count == 1
        assert validator.venv_install_attempts == 1

    def test_separate_workspaces_do_not_share_cache_state(self, tmp_path_factory):
        ws_a = tmp_path_factory.mktemp("ws_a")
        ws_b = tmp_path_factory.mktemp("ws_b")
        for ws in (ws_a, ws_b):
            _make_fake_venv(str(ws))
            (ws / "requirements.txt").write_text("requests==2.31.0\n", encoding="utf-8")
        validator_a = PolymorphicValidator(str(ws_a))
        validator_b = PolymorphicValidator(str(ws_b))
        with patch.object(
            validator_a, "_run_cmd_with_timeout",
            return_value={"returncode": 0, "stdout": "", "stderr": ""},
        ) as mock_a, patch.object(
            validator_b, "_run_cmd_with_timeout",
            return_value={"returncode": 0, "stdout": "", "stderr": ""},
        ) as mock_b:
            validator_a._resolve_python_interpreter()
            validator_a._resolve_python_interpreter()
            validator_b._resolve_python_interpreter()
        assert mock_a.call_count == 1
        assert mock_b.call_count == 1
        assert validator_a.venv_install_attempts == 1
        assert validator_b.venv_install_attempts == 1
        assert validator_a._venv_install_cache is not validator_b._venv_install_cache

    def test_venv_install_signature_content_derived_for_requirements_txt(self, tmp_path):
        req_path = tmp_path / "requirements.txt"
        req_path.write_text("requests==2.31.0\n", encoding="utf-8")
        validator = PolymorphicValidator(str(tmp_path))
        sig1 = validator._venv_install_signature(["-r", str(req_path)])
        req_path.write_text("requests==2.31.0\nflask==3.0\n", encoding="utf-8")
        sig2 = validator._venv_install_signature(["-r", str(req_path)])
        assert sig1 != sig2
        expected_digest = hashlib.sha256(req_path.read_bytes()).hexdigest()
        assert sig2 == ("-r", expected_digest)

    def test_venv_install_signature_is_content_derived_for_pyproject_list(self, tmp_path):
        validator = PolymorphicValidator(str(tmp_path))
        sig_a = validator._venv_install_signature(["requests==2.31.0"])
        sig_b = validator._venv_install_signature(["requests==2.31.0", "flask==3.0"])
        assert sig_a != sig_b


# ---------------------------------------------------------------------------
# R2 - SOURCE-2: absent known_target_context_items entry must not block
# SOURCE 3 (search-evidence) grounding the way a genuinely full view does.
# ---------------------------------------------------------------------------

def _anchor_failure_state(filepath: str, search_text: str) -> GenerationState:
    state = GenerationState()
    state.budgets.anchor_failure_counts[filepath] = 1
    state.last_failure = Failure(
        type="anchored_edit", message="x", raw_output="x",
        file_locations=[FileLocation(filepath=filepath)],
        likely_files=[filepath],
        attempted_edits=[{"search": search_text, "replace": "y"}],
        attempt=1,
    )
    return state


class _Ctx:
    def __init__(self, workspace_path: str):
        self.workspace_path = workspace_path
        self.worktree_path = workspace_path
        self.source_cache = SourceDerivationCache()


_INVOICE_CONTENT = (
    "class InvoiceCalculator:\n"
    "    def calculate_total(self, items):\n"
    "        subtotal = compute_subtotal(items)\n"
    "        return subtotal\n"
    "    def apply_discount(self, amount):\n"
    "        return amount * 0.9\n"
)
_INVOICE_SEARCH_TEXT = "subtotal = compute_subtotal(items)\n        return subtotal"


class TestSource2AnchoredEditGrounding:
    def test_no_recorded_context_item_still_permits_grounding(self, tmp_path):
        # The real gap this residual closes: an ABSENT known_target_context_
        # items entry (no evidence recorded at all) previously blocked
        # SOURCE 3 exactly like a positively-recorded full view would.
        filepath = "invoice.py"
        (tmp_path / filepath).write_text(_INVOICE_CONTENT, encoding="utf-8")
        ctx = _Ctx(str(tmp_path))
        state = _anchor_failure_state(filepath, _INVOICE_SEARCH_TEXT)
        assert filepath not in state.known_target_context_items
        result = _resolve_retry_member_hints(ctx, state, [filepath])
        assert result == {filepath: ["InvoiceCalculator.calculate_total"]}

    def test_recorded_skeleton_view_still_permits_grounding(self, tmp_path):
        filepath = "invoice.py"
        (tmp_path / filepath).write_text(_INVOICE_CONTENT, encoding="utf-8")
        ctx = _Ctx(str(tmp_path))
        state = _anchor_failure_state(filepath, _INVOICE_SEARCH_TEXT)
        state.known_target_context_items[filepath] = make_context_item(
            path=filepath, content="class InvoiceCalculator: ...", reason="x",
            source_type="named_in_request", trust_level="repository",
            tier="skeleton", is_exact=False, omitted_regions=True, revision="rev1",
        )
        result = _resolve_retry_member_hints(ctx, state, [filepath])
        assert result == {filepath: ["InvoiceCalculator.calculate_total"]}

    def test_recorded_full_view_still_blocks_grounding_nothing_to_escalate(self, tmp_path):
        filepath = "invoice.py"
        (tmp_path / filepath).write_text(_INVOICE_CONTENT, encoding="utf-8")
        ctx = _Ctx(str(tmp_path))
        state = _anchor_failure_state(filepath, _INVOICE_SEARCH_TEXT)
        state.known_target_context_items[filepath] = make_context_item(
            path=filepath, content=_INVOICE_CONTENT, reason="x",
            source_type="named_in_request", trust_level="repository",
            tier="full", is_exact=True, revision="rev1",
        )
        result = _resolve_retry_member_hints(ctx, state, [filepath])
        assert result == {}

    def test_ambiguous_search_text_never_grounds(self, tmp_path):
        # Two structurally unrelated (non-nested) methods both matching the
        # same distinctive tokens is genuine ambiguity - must stay unknown.
        filepath = "ambiguous.py"
        content = (
            "class Left:\n"
            "    def process_widget(self, widget):\n"
            "        return widget.value\n"
            "class Right:\n"
            "    def process_widget(self, widget):\n"
            "        return widget.value\n"
        )
        (tmp_path / filepath).write_text(content, encoding="utf-8")
        ctx = _Ctx(str(tmp_path))
        state = _anchor_failure_state(filepath, "return widget.value")
        result = _resolve_retry_member_hints(ctx, state, [filepath])
        assert result == {}

    def test_no_prior_anchor_failure_still_never_grounds(self, tmp_path):
        # anchor_failure_counts absent entirely (0) - a full-file rejection
        # that produced no anchor failure at all must still never fire.
        filepath = "invoice.py"
        (tmp_path / filepath).write_text(_INVOICE_CONTENT, encoding="utf-8")
        ctx = _Ctx(str(tmp_path))
        state = GenerationState()
        state.last_failure = Failure(
            type="anchored_edit", message="x", raw_output="x",
            file_locations=[FileLocation(filepath=filepath)],
            likely_files=[filepath],
            attempted_edits=[{"search": _INVOICE_SEARCH_TEXT, "replace": "y"}],
            attempt=1,
        )
        result = _resolve_retry_member_hints(ctx, state, [filepath])
        assert result == {}

    def test_stale_revision_grounding_still_reads_current_worktree_content(self, tmp_path):
        # Grounding always resolves against CURRENT worktree content, never
        # a stale in-memory copy - here the file has already moved past what
        # the rejected edit's search text describes, so it correctly fails
        # to ground rather than fabricating a match against old content.
        filepath = "invoice.py"
        (tmp_path / filepath).write_text(
            "class InvoiceCalculator:\n    def calculate_total(self, items):\n        return 0\n",
            encoding="utf-8",
        )
        ctx = _Ctx(str(tmp_path))
        state = _anchor_failure_state(filepath, _INVOICE_SEARCH_TEXT)
        result = _resolve_retry_member_hints(ctx, state, [filepath])
        assert result == {}

    def test_grounded_hint_never_widens_target_scope(self, tmp_path):
        # A path named only in attempted_edits/likely_files but NOT in the
        # retry's own authorized target_files must never be granted a hint.
        filepath = "invoice.py"
        (tmp_path / filepath).write_text(_INVOICE_CONTENT, encoding="utf-8")
        ctx = _Ctx(str(tmp_path))
        state = _anchor_failure_state(filepath, _INVOICE_SEARCH_TEXT)
        result = _resolve_retry_member_hints(ctx, state, ["other_file.py"])
        assert result == {}


# ---------------------------------------------------------------------------
# R3 - Planner max-token contract
# ---------------------------------------------------------------------------

class TestPlannerMaxTokenContract:
    @pytest.mark.asyncio
    async def test_planner_uses_configured_planner_max_tokens_not_general_default(self):
        cfg = AppConfig()
        cfg.llm.max_tokens = 4096
        cfg.llm.planner_max_tokens = 3333
        assert cfg.llm.max_tokens != cfg.llm.planner_max_tokens
        kernel = Kernel(config=cfg)
        llm = LLMClient(cfg)
        llm.complete = AsyncMock(return_value="Step 1: do the thing")
        engine = WorkflowEngine(kernel, llm)

        await engine.planner.run("plan this")

        _, kwargs = llm.complete.call_args
        assert kwargs["max_tokens_override"] == 3333

    @pytest.mark.asyncio
    async def test_absent_planner_specific_override_falls_back_to_configured_default(self):
        # planner_max_tokens is never actually absent (pydantic default
        # 8192, ge=256) - this proves the fallback chain still resolves to
        # THAT default correctly rather than silently to llm.max_tokens.
        cfg = AppConfig()
        cfg.llm.max_tokens = 1234
        assert cfg.llm.planner_max_tokens == 8192
        kernel = Kernel(config=cfg)
        llm = LLMClient(cfg)
        llm.complete = AsyncMock(return_value="Step 1")
        engine = WorkflowEngine(kernel, llm)

        await engine.planner.run("plan this")

        _, kwargs = llm.complete.call_args
        assert kwargs["max_tokens_override"] == 8192

    @pytest.mark.asyncio
    async def test_other_agents_retain_their_own_general_budget(self):
        cfg = AppConfig()
        cfg.llm.max_tokens = 4096
        cfg.llm.planner_max_tokens = 3333
        kernel = Kernel(config=cfg)
        llm = LLMClient(cfg)
        llm.complete = AsyncMock(return_value="Review: Approved")
        engine = WorkflowEngine(kernel, llm)
        # Reviewer never received a max_output_tokens at construction
        # (unchanged by this fix) - it must NOT pick up the Planner's own
        # stage-specific budget.
        assert engine.reviewer.max_output_tokens is None

        await engine.reviewer.run("review this")

        _, kwargs = llm.complete.call_args
        assert kwargs.get("max_tokens_override") is None

    def test_planner_construction_time_budget_is_an_explicit_int(self):
        cfg = AppConfig()
        cfg.llm.planner_max_tokens = 5555
        kernel = Kernel(config=cfg)
        llm = LLMClient(cfg)
        engine = WorkflowEngine(kernel, llm)
        assert engine.planner.max_output_tokens == 5555
