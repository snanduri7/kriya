"""PRD-035 harness controls, offline (no endpoint, no model): the C6 Developer-model spy must observe the adapter the ACTIVE
configuration names.

Defect (MEASURED 2026-11-05, preflight): ``test_c6_configured_fallback_transition`` registered its spy over the packaged ``ollama``
adapter. A configuration bound to ``kriya-ollama-native/3`` (``inference_runtime: ollama_native``) never reaches it, so the spy saw no
Developer request, ``developer_models`` stayed empty, and the injected compile failure ("until the attempt reaches the fallback")
never lifted: C6 failed by construction, whatever the model did. The control here uses a fake native adapter, so nothing contacts
an endpoint, and shows: the Developer request IS observed; the first compile failure IS injected; it is released only after the
fallback's Developer request; the old binding sees nothing and leaves the gate failing; and restoring the old binding (mutation) fails
the same assertions.
"""
import asyncio
import re
from pathlib import Path

import _certification_harness as harness
import pytest
from _certification_harness import developer_model_spy_installed, fail_first_compiles, fails_until_developer_reaches

from kriya.config.config import FallbackModelConfig, load_config
from kriya.core.inference_runtime import ChatRequest, ChatResponse, register_runtime_adapter, runtime_adapter
from kriya.core.model_runtime import OllamaNativeRuntimeAdapter, OllamaRuntimeAdapter, clear_model_runtime_cache
from kriya.tools.validate import PolymorphicValidator

PRIMARY = "qwen3-coder:30b-kriya-e52213655394"
FALLBACK = "qwen3.6:35b-a3b-q4_K_M-kriya-620d4d5ce36a"
DEVELOPER = "You are the Developer Agent. Write the file."  # role_of: "Developer Agent" = developer, "Developer agent" = investigation
PLANNER = "You are the Planner Agent. Plan the work."


class _FakeNative(OllamaNativeRuntimeAdapter):
    """The native adapter with the wire cut: a canned reply, no HTTP."""

    async def complete(self, client, request):
        return ChatResponse(content="ok", finish_reason="stop", completion_tokens=1, prompt_tokens=1)


@pytest.fixture(autouse=True)
def fake_native_runtime():
    packaged_native, packaged_default = runtime_adapter("ollama_native"), runtime_adapter("ollama")
    register_runtime_adapter(_FakeNative())
    clear_model_runtime_cache()
    yield
    register_runtime_adapter(packaged_native)
    register_runtime_adapter(packaged_default)
    clear_model_runtime_cache()


def _config(tmp_path, runtime="ollama_native"):
    operator = tmp_path / "operator.yaml"
    operator.write_text("{}\n", encoding="utf-8")
    config = load_config(str(operator))
    config.llm.model, config.llm.inference_runtime = PRIMARY, runtime
    config.llm_chain = [FallbackModelConfig(model=FALLBACK, base_url=config.llm.base_url, api_key=config.llm.api_key,
                                            inference_runtime=runtime)]
    return config


def _send(config, model, system=DEVELOPER):
    adapter = runtime_adapter(config.llm.inference_runtime or None)
    request = ChatRequest(model=model, messages=[{"role": "system", "content": system}, {"role": "user", "content": "go"}],
                          temperature=0.7, max_tokens=64)
    return asyncio.run(adapter.complete(None, request))


def _observe_developer_requests(config):
    """What the C6 harness does around the run, minus the run: the primary's Developer candidates, then the fallback's."""
    models = []
    with developer_model_spy_installed(config, models):
        _send(config, PRIMARY)
        _send(config, PRIMARY, system=PLANNER)  # another role: never recorded
        _send(config, PRIMARY)
        _send(config, FALLBACK)
    return models


def _assert_the_developer_requests_are_observed(models):
    assert models == [PRIMARY, PRIMARY, FALLBACK]


def test_the_spy_observes_the_developer_requests_of_a_native_bound_configuration(tmp_path):
    config = _config(tmp_path)
    assert type(runtime_adapter("ollama_native")) is _FakeNative
    _assert_the_developer_requests_are_observed(_observe_developer_requests(config))


def test_the_spy_follows_the_default_runtime_when_the_configuration_names_none(tmp_path):
    packaged = runtime_adapter("ollama")
    config = _config(tmp_path, runtime=None)
    assert config.llm.inference_runtime is None and type(runtime_adapter(None)) is type(packaged)
    models = []
    with developer_model_spy_installed(config, models):
        assert isinstance(runtime_adapter(None), OllamaRuntimeAdapter) and runtime_adapter(None) is not packaged
        # the default (openai-compat) adapter would need a real client: only the registration is checked here
    assert runtime_adapter("ollama") is packaged and models == []


def test_the_packaged_adapters_come_back_on_exit_even_after_an_error(tmp_path):
    config = _config(tmp_path)
    before = runtime_adapter("ollama_native")
    with pytest.raises(RuntimeError):
        with developer_model_spy_installed(config, []):
            assert runtime_adapter("ollama_native") is not before
            raise RuntimeError("the run failed")
    assert runtime_adapter("ollama_native") is before


def test_the_first_compile_failure_is_injected_and_released_only_after_the_fallbacks_developer_request(tmp_path, monkeypatch):
    config = _config(tmp_path)
    monkeypatch.setattr(PolymorphicValidator, "run_compile_check", lambda self, *a, **k: {"success": True, "output": ""})
    models = []
    with developer_model_spy_installed(config, models), fail_first_compiles(fails_until_developer_reaches(models, FALLBACK)):
        def gate():
            return PolymorphicValidator.run_compile_check(None)["success"]

        assert gate() is False  # no Developer interaction yet: the failure is injected
        _send(config, PRIMARY)
        assert gate() is False  # the primary's candidate: still failing
        _send(config, PRIMARY, system=PLANNER)
        assert gate() is False  # a different role never releases it
        _send(config, FALLBACK)
        assert gate() is True   # released only once the fallback's Developer request has been made
    assert PolymorphicValidator.run_compile_check(None)["success"] is True  # the patch is gone


def test_the_old_binding_over_the_packaged_ollama_adapter_sees_nothing_and_never_releases(tmp_path, monkeypatch):
    """The original C6 harness: a spy over the packaged ``ollama`` adapter."""
    config = _config(tmp_path)
    monkeypatch.setattr(PolymorphicValidator, "run_compile_check", lambda self, *a, **k: {"success": True, "output": ""})
    models = []

    class _OldSpy(OllamaRuntimeAdapter):
        async def complete(self, client, request):
            models.append(request.model)
            return await super().complete(client, request)

    packaged = runtime_adapter("ollama")
    register_runtime_adapter(_OldSpy())
    try:
        with fail_first_compiles(fails_until_developer_reaches(models, FALLBACK)):
            _send(config, PRIMARY)
            _send(config, FALLBACK)  # the native adapter serves both; the old spy is bypassed
            assert models == []
            assert PolymorphicValidator.run_compile_check(None)["success"] is False  # never released: C6 would fail by construction
    finally:
        register_runtime_adapter(packaged)


def test_mutation_restoring_the_old_binding_fails_the_observation(tmp_path, monkeypatch):
    """Restoring the assumption that the packaged ``ollama`` adapter is the one in use must fail the assertions above."""
    config = _config(tmp_path)
    monkeypatch.setattr(harness, "runtime_adapter", lambda name=None: runtime_adapter("ollama"))
    mutated = _observe_developer_requests(config)
    with pytest.raises(AssertionError):
        _assert_the_developer_requests_are_observed(mutated)


def test_the_c6_case_uses_the_shared_spy_and_not_the_old_binding():
    source = (Path(__file__).parent / "test_live_prd035_certification.py").read_text()
    c6 = source[source.index("def test_c6_configured_fallback_transition"):source.index('@certification_case("C7"')]
    assert "developer_model_spy_installed(case.config" in c6 and "fails_until_developer_reaches(" in c6
    assert not re.search(r'runtime_adapter\("ollama"\)|_DeveloperModelSpy\(OllamaRuntimeAdapter\)', source)


# --- C11: the Semgrep provider needs its digest-pinned image (S8, 6492c78: there is no host provider) -------------------
#
# Defect (MEASURED, matrix attempt 1, 2026-11-05): C11 configured only `version` and `rule_packs`; the adapter refuses without `providers.semgrep.image`, so
# C11 failed `static_analysis_unavailable / CONTAINMENT_UNAVAILABLE` before any commit, whatever the model did. The controls call the adapter's own precondition
# (`_containment_ready`, which runs before any container) with C11's REAL settings constant: no model, no Docker.

def _semgrep_refusal(settings, tmp_path):
    from kriya.static_analysis.adapters.semgrep import SemgrepAdapter, SemgrepSettings
    from kriya.static_analysis.port import ExecutionContext
    from kriya.tools.containment_oci import OCIContainmentBackend

    context = ExecutionContext(containment_backend=OCIContainmentBackend(), workspace_root=str(tmp_path))
    return SemgrepAdapter(SemgrepSettings(**settings), context)._containment_ready()  # noqa: SLF001 - the precondition the probe and the scan both enforce


def _c11_settings():
    import test_live_prd035_certification as live

    return live.SEMGREP_PROVIDER_SETTINGS


def _authoritative_semgrep_pins():
    manifest = (Path(__file__).parent / "certification" / "pinned_images.txt").read_text().splitlines()
    return [line for line in manifest if line.startswith("semgrep/semgrep@sha256:")]


def test_the_old_c11_configuration_without_an_image_is_refused_deterministically(tmp_path):
    old = {key: value for key, value in _c11_settings().items() if key != "image"}  # C11 exactly as it was written on 2026-09-27
    assert "providers.semgrep.image is not set" in _semgrep_refusal(old, tmp_path)


def test_the_corrected_c11_configuration_is_ready(tmp_path):
    assert _semgrep_refusal(dict(_c11_settings()), tmp_path) is None


def test_removing_the_image_from_the_c11_settings_brings_the_refusal_back(tmp_path, monkeypatch):
    import test_live_prd035_certification as live

    mutated = {key: value for key, value in live.SEMGREP_PROVIDER_SETTINGS.items() if key != "image"}
    monkeypatch.setattr(live, "SEMGREP_PROVIDER_SETTINGS", mutated)
    refusal = _semgrep_refusal(dict(_c11_settings()), tmp_path)
    assert refusal is not None and "image is not set" in refusal


@pytest.mark.parametrize("image", ["semgrep/semgrep:1.178.0", "semgrep/semgrep", "semgrep/semgrep@sha256:abc", "semgrep/semgrep@sha256:" + "G" * 64])
def test_an_unpinned_or_malformed_image_is_refused(tmp_path, image):
    with pytest.raises(ValueError, match="pinned by digest"):
        _semgrep_refusal({**_c11_settings(), "image": image}, tmp_path)


def test_the_c11_image_equals_the_single_authoritative_pin_in_pinned_images_txt():
    pins = _authoritative_semgrep_pins()
    assert len(pins) == 1 and _c11_settings()["image"] == pins[0]
    assert _c11_settings()["image"] != "semgrep/semgrep@sha256:" + "0" * 64  # a well-formed but wrong digest would not equal the pin


def test_the_c11_case_uses_the_shared_settings_constant():
    source = (Path(__file__).parent / "test_live_prd035_certification.py").read_text()
    c11 = source[source.index("def test_c11_static_analysis_enabled"):]
    assert 'providers={"semgrep": dict(SEMGREP_PROVIDER_SETTINGS)}' in c11 and '"rule_packs"' not in c11
