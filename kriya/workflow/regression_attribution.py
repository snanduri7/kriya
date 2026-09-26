"""VAL-001 G1-DEVINV2 (2026-09-20): resolves an ambiguous NOT_COMPARABLE
full-regression delta entry (kriya/workflow/validation_baseline.py::
classify_level2_delta - a test failing POST-candidate whose PRE-mutation
status this codebase's FAILED/ERROR-only pytest adapter cannot see, e.g.
because it wasn't in PRE's own failing set at all) by replaying that
specific test id, IN ISOLATION, against BOTH the real still-pristine
pre-mutation workspace (kriya/workflow/workflow.py's own `workspace_path` -
the full-regression gate runs before candidate changes are ever copied
into it, so it is guaranteed untouched at the point this is called) and
the candidate's own isolated sandbox worktree.

Deliberately a separate module from validation_baseline.py (which documents
itself as "a pure, deterministic comparison library - it runs no
subprocess, calls no model" - a replay is neither) and from
kriya/workflow/deterministic_failure_diagnostic.py (whose own docstring
scopes it tightly to "is the deterministic GATE ITSELF going to keep
failing identically regardless of the candidate," triggered by the SAME
failure recurring across attempts - a materially different question and
trigger condition from "does this ONE test id pass against pristine code,
right now, given a PRE-mutation baseline already captured once"). Reuses
that module's own `_copy_baseline_workspace` helper AS-IS rather than
duplicating it - identical exclusion set, identical safety properties
(read-only os.walk + shutil.copy2, temp dir always removed).

Replays EACH ambiguous id against BOTH an isolated pristine copy AND an
isolated copy of the candidate's own current content (never the shared
sandbox worktree itself - a separate temp copy each time) - checking
pristine alone is not sufficient. Live-confirmed during this fix's own
development: a test can fail during a real, full 5364-test suite run for
reasons having nothing to do with either the pristine OR the candidate code
(execution-order/shared-fixture-state artifacts of the full suite itself) -
for exactly the ambiguous test this fix's own motivating incident produced
(tests/test_extract.py::test_collect_files_skips_hidden), an isolated
single-test run PASSED against both pristine AND the actual candidate
content that had been accepted at Attempt 1, even though the SAME test
showed up as newly-failing in that attempt's own full-suite run. Checking
pristine alone would have misclassified this exact case as a confirmed
candidate regression (pristine doesn't reproduce it, so - wrongly -
"the candidate must have caused it"); checking both sides is what makes
"baseline passes + candidate CONSISTENTLY fails" a real, verified claim
rather than an assumption from one side of the comparison.

Fails closed by construction, but NOT by relabeling: an indeterminate
outcome - the replay itself errors/times out, OR the test passes against
BOTH pristine and candidate in isolation despite failing in the full-suite
run - is left classified NOT_COMPARABLE (its own already-honest "cannot be
compared" meaning), never reclassified as PRE_EXISTING_FAILURE. Passing
both sides in isolation proves only that THIS candidate cannot currently
be shown to cause it - not that it is a genuine, known, accepted
pre-existing condition (it could be test-order interaction, global-state
leakage, or any other non-reproducible cause). Overwriting it with
PRE_EXISTING_FAILURE would be a false historical claim, and - because that
label is also used for the honest "already known and understood" case
elsewhere in this codebase's own vocabulary - would corrupt exactly the
signal a future maintainer (or a future run of this same check) relies on
to tell "already characterized" apart from "never resolved." Structurally,
NOT_COMPARABLE already stays out of TERMINAL_BLOCKING_CLASSIFICATIONS on
its own (matching classify_level2_delta's own existing treatment of it),
so this costs nothing at the level2 layer; the caller's own separate
level1-driven REGRESSION_UNATTRIBUTED stop (kriya/workflow/workflow.py) is
what actually halts the run when nothing else is attributable - never a
side effect of this module quietly renaming an unknown into a known one.

Live incident this closes: a real G1 run (qwen3.8:27b, 2026-09-19/20)
produced a functionally correct Attempt-1 candidate (independently
confirmed correct via review external to this module and to Kriya's own
generation pipeline), then discarded it because the full-regression gate
blocked on a LEVEL1-only aggregate delta with a single ambiguous, unresolved test
(tests/test_extract.py::test_collect_files_skips_hidden, classified
NOT_COMPARABLE) - and the raw, unfiltered 142-failure pytest dump then fed
to the Developer for repair drove both the primary and fallback model into
repeated malformed responses across 6 further attempts. See
kriya/workflow/workflow.py's own REGRESSION_UNATTRIBUTED handling for what
happens when, even after this resolution step, nothing is confirmed
candidate-attributable."""
from __future__ import annotations

import logging
import shutil
import tempfile
from typing import Any, Dict, Sequence, Tuple

from kriya.workflow.validation_baseline import (
    DeltaClassification,
    parse_pytest_structured_outcomes,
)

logger = logging.getLogger(__name__)


def confirm_ambiguous_regressions(
    level2: Dict[str, DeltaClassification],
    *, pristine_workspace_path: str, candidate_workspace_path: str, autonomy_cfg: Any,
) -> Tuple[Dict[str, DeltaClassification], Dict[str, str]]:
    """For every level2 entry classified NOT_COMPARABLE, replays it in
    ISOLATION (this id alone, not the full suite) against BOTH an isolated
    pristine copy and an isolated copy of the candidate's own current
    content, to resolve the ambiguity - reclassifying it ONLY when the
    replay produces a real, confirmed answer, and leaving it NOT_COMPARABLE
    (never PRE_EXISTING_FAILURE, never NEW_FAILURE) whenever it doesn't:
      - fails against the candidate only (pristine passes in isolation) ->
        confirmed candidate-caused; reclassified NEW_FAILURE. Sent to the
        Developer for repair.
      - fails against BOTH -> a genuine, real, understood failure, but not
        caused by THIS candidate (pristine already has it too);
        reclassified PRE_EXISTING_FAILURE. Excluded from Developer repair.
      - fails against pristine only (candidate passes in isolation) -> not
        a regression by any reading (the candidate doesn't even reproduce
        the pristine failure); reclassified PRE_EXISTING_FAILURE. Excluded
        from Developer repair.
      - passes against BOTH in isolation, despite showing up as failing in
        the full-suite POST run -> UNATTRIBUTED/NON-REPRODUCIBLE: this
        proves only that the candidate cannot currently be shown to cause
        it, never that it's a known, accepted pre-existing condition (could
        be test-order interaction, shared-state leakage, or any other
        non-reproducible cause) - left classified NOT_COMPARABLE. Excluded
        from Developer repair (never sent as a "confirmed" anything), and
        the caller's own level1-driven REGRESSION_UNATTRIBUTED stop is what
        halts the run when nothing else is attributable - never silently
        waived by relabeling this into a historically-false "already knew
        about this" bucket.
      - the replay itself raises (subprocess/timeout/environment error) ->
        UNATTRIBUTED/INDETERMINATE for the same reason - left classified
        NOT_COMPARABLE. A broken replay is evidence of nothing and must
        never be read as either exoneration or confirmation.

    Returns (resolved_level2, evidence) where evidence maps test_id -> one
    human-readable line describing how it was resolved (or why it wasn't).
    `level2` itself is never mutated - a fresh dict is always returned."""
    ambiguous_ids = [
        test_id for test_id, cls in level2.items()
        if cls == DeltaClassification.NOT_COMPARABLE
    ]
    resolved = dict(level2)
    evidence: Dict[str, str] = {}
    if not ambiguous_ids:
        return resolved, evidence

    try:
        pristine_result = _replay_test_ids_against_workspace(
            test_ids=ambiguous_ids,
            workspace_path=pristine_workspace_path,
            autonomy_cfg=autonomy_cfg,
        )
        candidate_result = _replay_test_ids_against_workspace(
            test_ids=ambiguous_ids,
            workspace_path=candidate_workspace_path,
            autonomy_cfg=autonomy_cfg,
        )
    except Exception as exc:
        logger.warning(
            "Ambiguous-regression isolated replay failed (%s) for %d ambiguous "
            "test(s) - left classified NOT_COMPARABLE (indeterminate), never "
            "reclassified as PRE_EXISTING_FAILURE or NEW_FAILURE. The caller's own "
            "level1-driven REGRESSION_UNATTRIBUTED stop is what fails this closed "
            "when nothing else is confirmed attributable.", exc, len(ambiguous_ids),
        )
        for test_id in ambiguous_ids:
            # `resolved[test_id]` is deliberately left untouched (still
            # NOT_COMPARABLE, copied from `level2` above) - a broken replay
            # is evidence of nothing, and must never be read as either
            # exoneration OR confirmation.
            evidence[test_id] = (
                f"isolated replay itself failed ({exc}) - left NOT_COMPARABLE "
                "(indeterminate), not attributed either way"
            )
        return resolved, evidence

    pristine_parsed = parse_pytest_structured_outcomes(pristine_result.get("output", ""))
    candidate_parsed = parse_pytest_structured_outcomes(candidate_result.get("output", ""))
    pristine_failed_ids = {o.test_id for o in (pristine_parsed[0] if pristine_parsed else ())}
    candidate_failed_ids = {o.test_id for o in (candidate_parsed[0] if candidate_parsed else ())}
    for test_id in ambiguous_ids:
        fails_pristine = test_id in pristine_failed_ids
        fails_candidate = test_id in candidate_failed_ids
        if not fails_pristine and fails_candidate:
            resolved[test_id] = DeltaClassification.NEW_FAILURE
            evidence[test_id] = (
                "passes against an isolated pristine replay but fails against an "
                "isolated candidate replay - confirmed candidate-caused regression"
            )
        elif fails_pristine and fails_candidate:
            resolved[test_id] = DeltaClassification.PRE_EXISTING_FAILURE
            evidence[test_id] = (
                "fails against isolated replays of BOTH pristine and candidate - "
                "a real failure, but not attributable to this candidate"
            )
        elif fails_pristine and not fails_candidate:
            resolved[test_id] = DeltaClassification.PRE_EXISTING_FAILURE
            evidence[test_id] = (
                "fails against an isolated pristine replay but passes against an "
                "isolated candidate replay - not a regression"
            )
        else:
            # `resolved[test_id]` is deliberately left untouched (still
            # NOT_COMPARABLE). Passing both sides in isolation proves only
            # that THIS candidate cannot currently be shown to cause the
            # failure - not that it is a genuine, known, accepted
            # pre-existing condition (it could be test-order interaction,
            # global-state leakage, or any other non-reproducible cause).
            # Reclassifying it as PRE_EXISTING_FAILURE would be a false
            # historical claim and would corrupt that label's own meaning
            # elsewhere in this codebase.
            evidence[test_id] = (
                "passes against isolated replays of BOTH pristine and candidate, despite "
                "failing in the full-suite run - left NOT_COMPARABLE (indeterminate/"
                "non-reproducible), not attributed to this candidate and not reclassified "
                "as a known pre-existing failure"
            )
    return resolved, evidence


def _replay_test_ids_against_workspace(
    *, test_ids: Sequence[str], workspace_path: str, autonomy_cfg: Any,
) -> Dict[str, Any]:
    """Runs ONLY the given test ids, IN ISOLATION (not the full suite), for
    whichever workspace the caller names (pristine or candidate) - never
    mutates or reads back into it, always works against a fresh temp copy
    of it instead. Returns the same {"success": bool, "output": str} shape
    PolymorphicValidator.run_tests() already returns. The temp copy is
    always removed."""
    from kriya.tools.validate import PolymorphicValidator
    from kriya.workflow.deterministic_failure_diagnostic import _copy_baseline_workspace

    temp_dir = tempfile.mkdtemp(prefix="kriya-regression-replay-")
    try:
        _copy_baseline_workspace(workspace_path, temp_dir)
        validator = PolymorphicValidator(
            temp_dir, original_workspace_path=temp_dir, autonomy_cfg=autonomy_cfg,
        )
        return validator.run_tests(target_test=list(test_ids))
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
