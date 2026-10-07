"""QUALIFICATION-ARTIFACT-STABILITY-001: a qualification run may authorize
only evidence produced while the served runtime identity stayed the one it
is sealed under.

Incident (MEASURED 2026-10-07, Ollama 0.40.0): the qwen3.6 tag was rewritten
by the provider's automatic conversion while `kriya model qualify` was
alive; the fingerprint was probed once, the later cases ran against the new
artifact and the record attributed them to the old digest. These tests
reproduce that shape deterministically (scripted identity observations, a
scripted LLM, no provider) and pin the fail-closed behaviour at every
boundary, the sticky decision, the CLI refusal and the authority consumer.
"""
import asyncio
import json
import os
from dataclasses import replace

import pytest
from click.testing import CliRunner

from kriya.config import AppConfig
from kriya.core import model_qualification as mq
from kriya.core.completion import CompletionResult, CompletionStatus
from kriya.core.inference_settings import role_inference_settings
from kriya.core.model_runtime import ModelRuntimeFingerprint
from kriya.core.qualification_identity import (
    AFTER_CASE,
    BEFORE_CASE,
    CLOSE,
    START,
    QualificationIdentityGuard,
)

MODEL = "qwen3.6:35b"
ARTIFACT_A = "sha256:b2e941213e244ca516a5359f6a980e91511d097e44cdf8091666ddf7069b3482"
ARTIFACT_B = "sha256:0314ad2492d1fc443c33fc3026689db99374337a7c18887b44a865264b35ab78"
THREE_CASES = ["plain_completion", "finish_reason_stop", "structured_json"]
TWO_CASES = THREE_CASES[:2]


def _fp(artifact=ARTIFACT_A, **changes):
    fp = ModelRuntimeFingerprint(
        alias=MODEL, endpoint="http://localhost:11434/v1", provider="ollama", provider_version="0.40.0",
        artifact_digest=artifact, weights_digest="sha256:3f87bb0b", kriya_protocol="capabilities-sha256:x",
        server_parameters=("num_ctx 32768",),
    )
    return replace(fp, **changes)


def _result(content, **kw):
    kw.setdefault("finish_reason", "stop")
    return CompletionResult(status=CompletionStatus.OK, content=content, model=MODEL, **kw)


class FakeLLM:
    """Scripted results in call order; the last one repeats."""

    def __init__(self, *results):
        self.results = list(results) or [_result("READY")]
        self.calls = 0
        self.last_completion = None

    async def complete_result(self, system, user, stream_callback=None, **kw):
        self.calls += 1
        result = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if stream_callback is not None:
            stream_callback(result.content)
        self.last_completion = result
        return result


def _llm():
    return FakeLLM(_result("READY"), _result("Hello there."), _result('{"status": "ok", "count": 3}'))


class ScriptedIdentity:
    """The served artifact at each successive observation; the last repeats."""

    def __init__(self, *artifacts, **changes):
        self.artifacts = list(artifacts)
        self.changes = changes
        self.calls = 0

    def __call__(self):
        artifact = self.artifacts[min(self.calls, len(self.artifacts) - 1)]
        self.calls += 1
        return _fp(artifact, **self.changes)


def _run(observer, llm=None, only=TWO_CASES, progress=None):
    return asyncio.run(mq.run_qualification(AppConfig(), MODEL, llm=llm or _llm(), fingerprint=_fp(), only=only,
                                            identity_observer=observer, progress=progress))


def _settings():
    return role_inference_settings(AppConfig(), "developer", MODEL)


def _store_files():
    home = mq.qualification_home()
    return sorted(os.listdir(home)) if os.path.isdir(home) else []


# --- the matrix ----------------------------------------------------------------------------------------

def test_stable_identity_for_all_cases_qualifies():
    observer = ScriptedIdentity(ARTIFACT_A)
    record = _run(observer)
    assert [c["status"] for c in record["cases"]] == [mq.PASS, mq.PASS]
    stability = record["identity_stability"]
    # start, before/after each of two cases, close: every boundary observed, in order.
    assert [o["boundary"] for o in stability["observations"]] == [START, BEFORE_CASE, AFTER_CASE, BEFORE_CASE,
                                                                   AFTER_CASE, CLOSE]
    assert [o["capability"] for o in stability["observations"]] == [None, *TWO_CASES[:1] * 2, *TWO_CASES[1:] * 2, None]
    assert stability["stable"] is True and stability["change"] is None
    assert stability["baseline_digest"] == _fp().digest == record["fingerprint_digest"]
    assert all(o["digest"] == _fp().digest for o in stability["observations"])
    assert stability["cases_completed"] == TWO_CASES and observer.calls == 6
    # The sealed record is current authority for exactly this identity.
    mq.save_record(record)
    assert mq.assess(_fp(), ["plain_completion"], settings=_settings()).status == mq.QUALIFIED


def test_identity_differs_before_first_case_fails_closed():
    llm = _llm()
    with pytest.raises(mq.QualificationArtifactChangedError, match=mq.ARTIFACT_CHANGED) as raised:
        _run(ScriptedIdentity(ARTIFACT_B), llm)
    change = raised.value.diagnostic["identity_stability"]["change"]
    assert change["boundary"] == START and change["cases_completed"] == []
    assert change["baseline_digest"] == _fp().digest and change["observed_digest"] == _fp(ARTIFACT_B).digest
    assert llm.calls == 0, "no case may run against an identity that is not the baseline"
    assert _store_files() == []


def test_identity_changes_between_cases_fails_closed():
    # start A, before/after case 1 A, before case 2 B.
    llm = _llm()
    reported = []
    with pytest.raises(mq.QualificationArtifactChangedError) as raised:
        _run(ScriptedIdentity(ARTIFACT_A, ARTIFACT_A, ARTIFACT_A, ARTIFACT_B), llm, progress=reported.append)
    change = raised.value.diagnostic["identity_stability"]["change"]
    assert (change["boundary"], change["capability"]) == (BEFORE_CASE, "finish_reason_stop")
    assert change["cases_completed"] == ["plain_completion"]
    assert [r.capability for r in reported] == ["plain_completion"]  # attributed under A, reported as such
    assert llm.calls == 1 and _store_files() == []


def test_identity_changes_during_a_case_fails_closed_and_drops_that_case():
    # start A, before case 1 A, after case 1 B: the case ran, its evidence is never attributed.
    llm = _llm()
    reported = []
    with pytest.raises(mq.QualificationArtifactChangedError) as raised:
        _run(ScriptedIdentity(ARTIFACT_A, ARTIFACT_A, ARTIFACT_B), llm, progress=reported.append)
    stability = raised.value.diagnostic["identity_stability"]
    assert (stability["change"]["boundary"], stability["change"]["capability"]) == (AFTER_CASE, "plain_completion")
    assert stability["change"]["cases_completed"] == [] and stability["cases_completed"] == []
    assert reported == [], "a case whose identity changed underneath it is never reported as a verdict"
    assert llm.calls == 1
    assert [o["digest"] for o in stability["observations"]] == [_fp().digest, _fp().digest, _fp(ARTIFACT_B).digest]


def test_identity_changes_after_the_final_case_before_the_seal_fails_closed():
    # Five matching observations (start, 2 x before/after), then B at close.
    llm = _llm()
    with pytest.raises(mq.QualificationArtifactChangedError) as raised:
        _run(ScriptedIdentity(*([ARTIFACT_A] * 5), ARTIFACT_B), llm)
    change = raised.value.diagnostic["identity_stability"]["change"]
    assert change["boundary"] == CLOSE and change["cases_completed"] == TWO_CASES
    assert llm.calls == 2 and _store_files() == []


def test_a_provider_reload_serving_the_same_identity_is_allowed():
    """Every observation is a fresh probe of a reloaded server: a new object each time, same identity."""
    seen = []

    def observer():
        fingerprint = _fp(probe_errors=(f"reload {len(seen)}",))  # diagnostics only, never identity
        seen.append(fingerprint)
        return fingerprint

    record = _run(observer)
    assert len({id(f) for f in seen}) == 6 and len({f.digest for f in seen}) == 1
    assert record["identity_stability"]["stable"] is True


def test_volatile_metadata_changes_never_invalidate():
    """Timestamps and probe diagnostics change between observations; the identity does not."""
    record = _run(ScriptedIdentity(ARTIFACT_A, probe_errors=("transient /api/ps timeout",)))
    observations = record["identity_stability"]["observations"]
    assert record["identity_stability"]["stable"] is True
    assert all(o["probe_errors"] == ["transient /api/ps timeout"] for o in observations)
    assert all(o["observed_at"] for o in observations)


def test_an_identity_that_returns_to_the_baseline_stays_invalid():
    guard = QualificationIdentityGuard(_fp(), ScriptedIdentity(ARTIFACT_A, ARTIFACT_B, ARTIFACT_A, ARTIFACT_A))
    assert guard.observe(START) is None
    change = guard.observe(BEFORE_CASE, "plain_completion")
    assert change is not None and change.observed_digest == _fp(ARTIFACT_B).digest
    # Back to A: the decision is sticky and nothing is observed again.
    assert guard.observe(AFTER_CASE, "plain_completion") is change
    assert guard.observe(CLOSE) is change
    assert len(guard.observations) == 2 and guard.stable is False
    assert guard.evidence()["change"]["observed_digest"] == _fp(ARTIFACT_B).digest
    # And through the run: A, A, A, B, A, A... still refused.
    with pytest.raises(mq.QualificationArtifactChangedError):
        _run(ScriptedIdentity(ARTIFACT_A, ARTIFACT_A, ARTIFACT_A, ARTIFACT_B, ARTIFACT_A))


def test_an_aborted_qualification_cannot_satisfy_model_status_authority(tmp_path, monkeypatch):
    """The CLI, with the default observer (a fresh probe of the binding): the
    served identity moves A -> B after the first case. Exit 1, the diagnostic
    on stderr and in --out, nothing in the store, and the authority consumer
    (`assess`, what `kriya model status` / doctor / routing read) sees MISSING
    for both identities."""
    from kriya.cli import main
    from kriya.core import llm as llm_module
    from kriya.core import model_runtime

    cfg = AppConfig()
    cfg.llm.model = MODEL
    monkeypatch.setattr("kriya.cli._model_cfg", lambda ctx: cfg)
    # Resolves in order: the baseline probe, the capability profile's cached
    # lookup (fresh=False, a cache hit in production), then the guard's fresh
    # observations: start, before/after case 1 (all A), before case 2 (B).
    served = ScriptedIdentity(*([ARTIFACT_A] * 5), ARTIFACT_B)
    probes = []

    def resolve(config, model=None, *, fresh=False, **kw):
        probes.append(fresh)
        return served()

    monkeypatch.setattr(model_runtime, "resolve_configured_model_runtime", resolve)
    monkeypatch.setattr(llm_module, "LLMClient", lambda config: FakeLLM(_result("READY")))
    out = tmp_path / "diagnostic.json"
    result = CliRunner().invoke(main, ["model", "qualify", "--out", str(out)])
    assert result.exit_code == 1, result.output
    assert mq.ARTIFACT_CHANGED in result.output and "1 case(s) had passed under the baseline" in result.output
    assert probes == [True, False, True, True, True, True], "every guard observation is a fresh probe"
    diagnostic = json.loads(out.read_text())
    assert diagnostic["status"] == mq.ARTIFACT_CHANGED and diagnostic["model"] == MODEL
    assert diagnostic["identity_stability"]["change"]["cases_completed"] == ["plain_completion"]
    assert "qualification_identity" not in diagnostic and "fingerprint_digest" not in diagnostic
    assert _store_files() == []
    for artifact in (ARTIFACT_A, ARTIFACT_B):
        assessment = mq.assess(_fp(artifact), ["plain_completion"], settings=_settings())
        assert assessment.status == mq.MISSING, assessment


def test_a_diagnostic_or_unstable_record_in_the_store_is_never_authority():
    """Defence in depth: should the --out diagnostic, or a record whose own
    identity evidence says unstable, ever be copied under a record key, the
    consumer still refuses it."""
    record = _run(ScriptedIdentity(ARTIFACT_A))
    settings = _settings()
    unstable = {**record, "identity_stability": {**record["identity_stability"], "stable": False}}
    current, reasons = mq.record_is_current(unstable, _fp(), settings)
    assert not current and any("not stable" in r for r in reasons)
    mq.save_record(unstable)
    assert mq.assess(_fp(), ["plain_completion"], settings=settings).status == mq.STALE
    diagnostic = {"status": mq.ARTIFACT_CHANGED, "model": MODEL, "identity_stability": unstable["identity_stability"],
                  "qualification_identity": record["qualification_identity"]}
    mq.save_record(diagnostic)
    assert mq.assess(_fp(), ["plain_completion"], settings=settings).status != mq.QUALIFIED


def test_historical_records_without_identity_evidence_stay_current():
    """Backward compatibility: every record sealed before this guard carries no
    identity_stability (or null); it is judged exactly as before."""
    settings = _settings()
    legacy = mq.build_record(_fp(), [mq.CaseResult("plain_completion", mq.PASS)], settings=settings)
    assert legacy["identity_stability"] is None
    assert mq.record_is_current(legacy, _fp(), settings) == (True, [])
    del legacy["identity_stability"]  # a record written by an earlier Kriya
    assert mq.record_is_current(legacy, _fp(), settings) == (True, [])
    mq.save_record(legacy)
    assert mq.assess(_fp(), ["plain_completion"], settings=settings).status == mq.QUALIFIED


# --- the incident, reproduced ------------------------------------------------------------------------------

def _incident():
    """Three cases pass under artifact A; the tag then resolves to artifact B
    (observed at the next pre-case boundary); the remaining cases attempt to run."""
    return ScriptedIdentity(*([ARTIFACT_A] * 7), ARTIFACT_B)


def test_the_qwen36_incident_shape_invalidates_the_qualification():
    llm = _llm()
    with pytest.raises(mq.QualificationArtifactChangedError) as raised:
        _run(_incident(), llm, only=THREE_CASES + ["multiline_json"])
    stability = raised.value.diagnostic["identity_stability"]
    assert stability["cases_completed"] == THREE_CASES
    assert (stability["change"]["boundary"], stability["change"]["capability"]) == (BEFORE_CASE, "multiline_json")
    assert stability["change"]["observed_digest"] == _fp(ARTIFACT_B).digest
    assert llm.calls == 3, "no further case ran against artifact B"
    assert _store_files() == [], "no authoritative record exists for either artifact"


def test_negative_control_without_the_guard_the_incident_is_attributed_to_artifact_a(monkeypatch):
    """Mutation: bypass the single guard call site. The same scripted run then
    seals four cases under artifact A although the fourth ran under B - the
    pre-fix defect - so the reproducer above fails without the fix."""
    monkeypatch.setattr(mq, "_require_stable_identity", lambda *args, **kwargs: None)
    llm = _llm()
    record = _run(_incident(), llm, only=THREE_CASES + ["multiline_json"])
    assert record["fingerprint_digest"] == _fp(ARTIFACT_A).digest and len(record["cases"]) == 4 and llm.calls == 4


def test_the_guard_rejects_an_unknown_boundary():
    with pytest.raises(ValueError, match="unknown qualification boundary"):
        QualificationIdentityGuard(_fp(), ScriptedIdentity(ARTIFACT_A)).observe("during_case")
