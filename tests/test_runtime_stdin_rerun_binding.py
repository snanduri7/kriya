"""Demo V2 Phase 0-B: a reviewed runtime subject is EXECUTED COMMAND + CWD + INPUT CHANNEL + STDIN SEMANTICS.

Traced (attempt.py, the mutating path's inline runtime block): the first run passes ``stdin_payload=rv_stdin_payload`` (the
payload the RuntimeCommandSubject fingerprint binds through ``stdin_sha256``); the self-correction re-run of the SAME
resolved commands, which the same approval covers, used to call ``run_app_sequence`` without it, so a reviewed non-empty
stdin subject executed with EMPTY stdin the second time. Reproduced through the real re-run path (scripted repair, rule 4).
"""
from unittest.mock import patch

import pytest
from test_repair_003_f1_residual_self_correction_rerun_environment_stop import (
    GRADE_FAILED,
    RUN_APP,
    _mutating_ctx,
    _ScriptedRepair,
)
from test_repair_003_f1_runtime_environment_stop import _VenvCreationFails

from kriya.tools.validate import PolymorphicValidator
from kriya.workflow.attempt import _RUNTIME_VERIFICATION_SYNTHETIC_INPUT, run_attempt
from kriya.workflow.failure import QualityGateFailure
from kriya.workflow.state import GenerationState


async def _run_with_rerun(tmp_path, input_channel):
    ctx, _developer, run_verifier = _mutating_ctx(tmp_path, RUN_APP, grades=[GRADE_FAILED, GRADE_FAILED])
    run_verifier.judge.return_value = {**run_verifier.judge.return_value, "input_channel": input_channel}
    repair = _ScriptedRepair(lambda _worktree: [])  # resolved: the loop ran, so the re-run happens
    original = PolymorphicValidator.run_app_sequence
    stdin_seen = []

    def spy(self, commands, *args, **kwargs):
        stdin_seen.append(kwargs.get("stdin_payload"))
        return original(self, commands, *args, **kwargs)

    stub = _VenvCreationFails()
    with patch.object(PolymorphicValidator, "_run_cmd_with_timeout", new=stub.method), \
            patch.object(PolymorphicValidator, "run_app_sequence", new=spy), \
            patch("kriya.workflow.self_correction.run_self_correction_loop", new=repair):
        with pytest.raises(QualityGateFailure):
            await run_attempt(GenerationState(), ctx)
    assert len(repair.calls) == 1
    return stdin_seen


@pytest.mark.asyncio
async def test_rerun_after_self_correction_carries_the_reviewed_stdin(tmp_path):
    seen = await _run_with_rerun(tmp_path, "stdin")
    assert seen == [_RUNTIME_VERIFICATION_SYNTHETIC_INPUT, _RUNTIME_VERIFICATION_SYNTHETIC_INPUT], seen


@pytest.mark.asyncio
async def test_rerun_without_an_input_channel_still_passes_no_stdin(tmp_path):
    """Negative control: nothing reviewed, nothing supplied - the re-run is unchanged."""
    seen = await _run_with_rerun(tmp_path, "none")
    assert seen == [None, None], seen
