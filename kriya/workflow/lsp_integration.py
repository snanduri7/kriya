"""Optional jdtls (Eclipse JDT Language Server) integration for deterministic Java diagnostics grounding in the retry loop. Extracted from kriya/workflow/workflow.py (2026-08-11 modularization)."""

import asyncio
import difflib
import hashlib
import logging
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)


async def _get_or_start_jdtls_client(existing_client: Any, project_root: str) -> Any:
    """Lazily starts a JdtlsClient for this generation run if jdtls is
    available, reusing an already-started one across retries rather than
    spinning up a fresh process per attempt (real, observed startup/indexing
    cost, up to ~2 minutes on larger codebases). Returns None (never raises)
    if jdtls isn't found on PATH or fails to start - clean, silent degrade
    to no LSP grounding, matching how Kriya treats every other optional
    local capability (`kriya doctor` reports whether jdtls was found;
    nothing here requires it)."""
    if existing_client is not None:
        return existing_client
    from kriya.tools.lsp import JdtlsClient, find_jdtls
    jdtls_path = find_jdtls()
    if not jdtls_path:
        # INFO, not silent: even a successful "ran fine, found nothing worth
        # surfacing" check produces zero log output by design (that's not a
        # failure), which made "LSP grounding never engaged this run" and
        # "it engaged and just had nothing to add" indistinguishable from the
        # log - confirmed live, 2026-08-03: a real, LSP-catchable missing-
        # import compile error occurred and NOTHING in the run's log (not
        # even the WARNING-level failure path above) explained whether jdtls
        # ever got a chance to check it at all. This one-time positive/
        # negative signal removes that ambiguity going forward.
        logger.info("jdtls not found on PATH - no LSP grounding for this run.")
        return None
    client = JdtlsClient(project_root, jdtls_path)
    try:
        await client.start()
        logger.info(f"jdtls started successfully ({jdtls_path}) - LSP grounding active for this run.")
        return client
    except Exception as ex:
        # WARNING, not debug: jdtls being found on PATH but failing to start is
        # a real, actionable gap in LSP grounding for this run - a silent debug-
        # level swallow here (confirmed live, 2026-08-03: a real "cannot find
        # symbol" compile error went completely uncaught by LSP grounding, and
        # this exact log line was the only place that could have explained why,
        # but was invisible at the default log level) leaves no way to tell
        # "not installed" (expected, silent) apart from "installed but broken"
        # (a real problem worth knowing about) from the run's own output.
        logger.warning(f"jdtls found but failed to start, proceeding without LSP grounding: {ex}")
        return None


async def _build_lsp_diagnostics_context(client: Any, worktree_path: str, filepaths: Iterable[str]) -> Dict[str, str]:
    """For each .java file in filepaths, checks its CURRENT worktree content
    against jdtls's real, resolved type graph and returns {filepath:
    formatted diagnostic text} for any with real (severity=Error) issues -
    see kriya/tools/lsp.py::format_diagnostics_for_prompt for the actual
    prompt framing. Best-effort per file: one file's check failing (timeout,
    a transient jdtls error) never blocks the others or raises - the caller
    merges this into the same error_source_context dict the retry loop
    already threads into the scoped per-file prompt, so a jdtls hiccup just
    means that one file's prompt has one less (still purely additive) signal
    this attempt, never a hard failure."""
    from kriya.tools.lsp import format_diagnostics_for_prompt
    context: Dict[str, str] = {}
    for filepath in filepaths:
        if not filepath.endswith(".java"):
            continue
        full_path = os.path.join(worktree_path, filepath)
        if not os.path.exists(full_path):
            continue
        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.read()
            diagnostics = await client.check_file(full_path, content)
            formatted = format_diagnostics_for_prompt(filepath, diagnostics)
            if formatted:
                context[filepath] = formatted
        except Exception as ex:
            # WARNING, not debug - same visibility reasoning as
            # _get_or_start_jdtls_client's own start() failure above: a per-file
            # check failing here is exactly the kind of thing worth knowing
            # about when a compile error that should have been LSP-grounded
            # wasn't.
            logger.warning(f"LSP check failed for '{filepath}': {ex}")
    return context
