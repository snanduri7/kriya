import os

import pytest
import yaml

from kriya.config.authority import ConfigSource
from kriya.config.config import (
    PRODUCTION_FIXED_RUNTIME_GUARANTEES,
    PRODUCTION_GENERATION_TIME_BUDGET_SECONDS,
    AppConfig,
    load_config,
    resolve_config_state,
    runtime_profile_preset_fields,
)


def test_load_default_config():
    # Load with no path, should load default_config.yaml automatically
    cfg = load_config()
    assert isinstance(cfg, AppConfig)
    assert cfg.llm.provider == "openai"
    assert cfg.llm.base_url == "http://localhost:11434/v1"
    
    # Read default_config.yaml dynamically as a reference to check
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ref_path = os.path.join(base_dir, "kriya", "config", "default_config.yaml")
    if os.path.exists(ref_path):
        with open(ref_path, "r", encoding="utf-8") as f:
            ref_data = yaml.safe_load(f) or {}
        expected_model = ref_data.get("llm", {}).get("model")
        if expected_model:
            assert cfg.llm.model == expected_model
            return
            
    assert isinstance(cfg.llm.model, str) and len(cfg.llm.model) > 0

def test_routing_enabled_by_default():
    """Locks in the deliberate 2026-08-02 default flip: routing.enabled is now
    True out of the box (was False). Explicit commands are unaffected either
    way - routing only activates when a typed REPL line's first word doesn't
    already match a real command name (kriya/repl.py::_route_line)."""
    assert AppConfig().routing.enabled is True
    assert load_config().routing.enabled is True


def test_workflow_controller_stays_disabled_by_default():
    """TRIED AND REVERTED same day (2026-08-24): enabled=true was briefly
    the default, reasoned as zero-risk since shadow mode never affects real
    generation output. That missed a real regression - shadow mode makes
    its own real Planner/Architect/SubtaskExecutor LLM calls through the
    real WorkflowEngine, independent of run_generation_workflow(). A common
    test pattern in this suite (tests/test_file_goal.py and others) mocks
    ONLY WorkflowEngine.run_generation_workflow, not Kernel/LLMClient -
    previously sufficient to guarantee zero real network calls end to end.
    With workflow_controller.enabled=true by default, that same pattern let
    shadow's own agent calls silently reach a real, unmocked LLMClient,
    hanging/failing against a live endpoint the test never expected to hit
    (confirmed live: tests/test_cli_smoke.py and tests/test_file_goal.py
    both broke/hung). Reverted before ever reaching origin. This test locks
    the safe default back in - see kriya/config/config.py::
    WorkflowControllerConfig's own docstring for the full account before
    trying this again; it needs a real test-suite audit first (every
    run_generation_workflow-only mock site), not a casual re-flip."""
    assert AppConfig().workflow_controller.enabled is False
    assert AppConfig().workflow_controller.mode == "shadow"
    assert load_config().workflow_controller.enabled is False
    assert load_config().workflow_controller.mode == "shadow"


def test_runtime_profile_defaults_to_none_and_changes_nothing():
    """runtime_profile (2026-08-25, external review P2) - the default
    (unset) must leave every underlying field exactly as it already
    behaves, matching every existing kriya.yaml unchanged."""
    assert AppConfig().runtime_profile is None
    cfg = load_config()
    assert cfg.runtime_profile is None
    assert cfg.workflow_controller.enabled is False
    assert cfg.engineering_triage.shadow_mode is True
    assert cfg.process_profiles.enabled is False


def test_runtime_profile_hardened_overrides_the_documented_fields():
    # SEC-009 P1: runtime_profile is PLATFORM_POLICY - a repository-sourced
    # kriya.yaml can no longer set it through load_config() at all (see
    # tests/test_sec009_config_authority.py for that denial coverage, which
    # this test previously exercised unintentionally via a tmp_path config
    # file). This test's actual subject - what the "hardened" preset expands
    # to - is exercised directly via the extracted mapping function
    # load_config() itself calls, independent of the authority gate.
    from kriya.config.config import runtime_profile_preset_fields

    fields = runtime_profile_preset_fields("hardened")

    assert fields[("workflow_controller", "enabled")] is True
    assert fields[("workflow_controller", "mode")] == "enforce"
    assert fields[("engineering_triage", "shadow_mode")] is False
    assert fields[("process_profiles", "enabled")] is True


def test_runtime_profile_hardened_does_not_touch_execution_policy_mode():
    """execution_policy.mode is a distinct, separately authorized decision
    (POL-001-P2, 2026-09-10) - the "hardened" preset does not silently
    reach around or override whatever a project explicitly set it to,
    in either direction."""
    cfg_audit = AppConfig(runtime_profile="hardened")
    assert cfg_audit.execution_policy.mode == "audit"

    cfg_enforce = AppConfig(runtime_profile="hardened", execution_policy={"mode": "enforce"})
    assert cfg_enforce.execution_policy.mode == "enforce"


def test_runtime_profile_rejects_an_unknown_value():
    with pytest.raises(Exception):
        AppConfig(runtime_profile="turbo")


def test_runtime_profile_rejects_conflicting_individual_settings(tmp_path):
    config_file = tmp_path / "kriya.yaml"
    with open(config_file, "w") as f:
        yaml.dump({
            "runtime_profile": "hardened",
            "workflow_controller": {"enabled": False, "mode": "shadow"},
        }, f)

    with pytest.raises(ValueError, match="cannot be combined"):
        load_config(str(config_file))


@pytest.mark.parametrize("profile", ["legacy", "validated", "hardened"])
def test_runtime_profile_accepts_supported_presets(profile):
    assert AppConfig(runtime_profile=profile).runtime_profile == profile


def test_runtime_profile_production_expands_to_sealed_safe_posture(tmp_path):
    config_file = tmp_path / "operator-production.yaml"
    config_file.write_text("runtime_profile: production\n", encoding="utf-8")

    state = resolve_config_state(str(config_file))
    cfg = AppConfig(**state.config_dict)

    assert cfg.runtime_profile == "production"
    assert cfg.workflow_controller.enabled is True
    assert cfg.workflow_controller.mode == "enforce"
    assert cfg.execution_policy.enabled is True
    assert cfg.execution_policy.mode == "enforce"
    assert cfg.autonomy.egress_policy == "local_only"
    assert cfg.autonomy.generation_time_budget_seconds == PRODUCTION_GENERATION_TIME_BUDGET_SECONDS
    assert cfg.autonomy.containment_backend == "oci"
    assert cfg.autonomy.contained_execution_required is True
    assert cfg.autonomy.mcp_contained_execution_required is True
    assert cfg.autonomy.brownfield_full_regression_baseline_policy == "required"
    # PRD-028 owns broad language-support proof; production must not pretend
    # that unsupported semantic precision is enforced today.
    assert cfg.autonomy.semantic_region_enforcement_required is False
    assert PRODUCTION_FIXED_RUNTIME_GUARANTEES == {
        "candidate_isolation_fail_closed",
        "checkpoint_persistence",
        "trace_persistence",
        "no_uncontained_host_fallback",
    }


@pytest.mark.parametrize(
    ("section", "field", "unsafe", "required"),
    [
        ("workflow_controller", "mode", "shadow", "enforce"),
        ("execution_policy", "mode", "audit", "enforce"),
        ("execution_policy", "enabled", False, True),
        ("autonomy", "egress_policy", "unrestricted", "local_only"),
        ("autonomy", "generation_time_budget_seconds", None, PRODUCTION_GENERATION_TIME_BUDGET_SECONDS),
        ("autonomy", "generation_time_budget_seconds", 7200, PRODUCTION_GENERATION_TIME_BUDGET_SECONDS),
        ("autonomy", "generation_time_budget_seconds", 0, PRODUCTION_GENERATION_TIME_BUDGET_SECONDS),
        ("autonomy", "generation_time_budget_seconds", True, PRODUCTION_GENERATION_TIME_BUDGET_SECONDS),
        ("autonomy", "contained_execution_required", False, True),
        ("autonomy", "mcp_contained_execution_required", False, True),
        ("autonomy", "containment_backend", "none", "oci"),
        ("autonomy", "brownfield_full_regression_baseline_policy", "auto", "required"),
    ],
)
def test_runtime_profile_production_rejects_contradictory_overrides(
    tmp_path, section, field, unsafe, required
):
    config_file = tmp_path / f"unsafe-{section}-{field}.yaml"
    config_file.write_text(
        yaml.safe_dump({"runtime_profile": "production", section: {field: unsafe}}),
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match=rf"production.*{section}\.{field}=.*requires {required!r}",
    ):
        resolve_config_state(str(config_file))


def test_runtime_profile_production_allows_matching_and_unrelated_overrides(tmp_path):
    config_file = tmp_path / "safe-production.yaml"
    config_file.write_text(
        yaml.safe_dump({
            "runtime_profile": "production",
            "execution_policy": {"mode": "enforce"},
            "autonomy": {
                "brownfield_full_regression_baseline_policy": "required",
                "semantic_region_enforcement_required": False,
            },
        }),
        encoding="utf-8",
    )

    cfg = AppConfig(**resolve_config_state(str(config_file)).config_dict)
    assert cfg.execution_policy.mode == "enforce"
    assert cfg.autonomy.brownfield_full_regression_baseline_policy == "required"
    assert cfg.autonomy.semantic_region_enforcement_required is False


def test_runtime_profile_production_keeps_a_stricter_explicit_deadline(tmp_path):
    """A shorter deadline is safer than the sealed 3600s: it is accepted, kept
    (never loosened back to the preset) and keeps its own provenance."""
    config_file = tmp_path / "strict-production.yaml"
    config_file.write_text(
        yaml.safe_dump({"runtime_profile": "production", "autonomy": {"generation_time_budget_seconds": 1800}}),
        encoding="utf-8",
    )
    state = resolve_config_state(str(config_file))
    cfg = AppConfig(**state.config_dict)
    assert cfg.autonomy.generation_time_budget_seconds == 1800
    sources = {violation.field_path: violation.source for violation in state.violations}
    assert sources.get("autonomy.generation_time_budget_seconds") is not ConfigSource.RUNTIME_PROFILE_OVERRIDE
    assert sources["autonomy.egress_policy"] is ConfigSource.RUNTIME_PROFILE_OVERRIDE


def test_direct_production_app_config_accepts_only_an_equal_or_stricter_deadline():
    sealed = {
        top: {leaf: value for (t, leaf), value in runtime_profile_preset_fields("production").items() if t == top}
        for top in {top for top, _ in runtime_profile_preset_fields("production")}
    }
    assert AppConfig(runtime_profile="production", **sealed).autonomy.generation_time_budget_seconds == 3600
    stricter = {**sealed, "autonomy": {**sealed["autonomy"], "generation_time_budget_seconds": 60}}
    assert AppConfig(runtime_profile="production", **stricter).autonomy.generation_time_budget_seconds == 60
    for unsafe in ({"generation_time_budget_seconds": 7200}, {"generation_time_budget_seconds": None},
                   {"egress_policy": "unrestricted"}):
        weakened = {**sealed, "autonomy": {**sealed["autonomy"], **unsafe}}
        with pytest.raises(Exception, match="production.*is sealed"):
            AppConfig(runtime_profile="production", **weakened)


def test_direct_app_config_cannot_claim_production_with_unsafe_defaults():
    with pytest.raises(Exception, match="production.*execution_policy.mode"):
        AppConfig(runtime_profile="production")


def test_existing_runtime_profile_mappings_remain_byte_for_byte_compatible():
    assert runtime_profile_preset_fields("legacy") == {
        ("engineering_triage", "shadow_mode"): True,
        ("process_profiles", "enabled"): False,
        ("workflow_controller", "enabled"): False,
        ("workflow_controller", "mode"): "shadow",
    }
    assert runtime_profile_preset_fields("validated") == {
        ("engineering_triage", "shadow_mode"): False,
        ("process_profiles", "enabled"): True,
        ("workflow_controller", "enabled"): True,
        ("workflow_controller", "mode"): "shadow",
    }
    assert runtime_profile_preset_fields("hardened") == {
        ("engineering_triage", "shadow_mode"): False,
        ("process_profiles", "enabled"): True,
        ("workflow_controller", "enabled"): True,
        ("workflow_controller", "mode"): "enforce",
    }


def test_load_custom_config(tmp_path):
    # SEC-009 P1: llm.base_url is SECURITY_AUTHORITY (it redirects where
    # Kriya sends requests) and this config_file lives outside the test's
    # CWD/workspace, so it is no longer authorized to set it - see
    # tests/test_sec009_config_authority.py for the dedicated coverage of
    # that denial. This test keeps only the REPOSITORY_SAFE fields it was
    # actually exercising (model name, log level).
    custom_yaml = {
        "llm": {
            "provider": "openai",
            "model": "mistral-7b",
        },
        "logging": {
            "level": "DEBUG"
        }
    }

    config_file = tmp_path / "custom_config.yaml"
    with open(config_file, "w") as f:
        yaml.dump(custom_yaml, f)

    cfg = load_config(str(config_file))

    assert cfg.llm.model == "mistral-7b"
    assert cfg.logging.level == "DEBUG"
    # Fallback/default still applies to other items
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ref_path = os.path.join(base_dir, "kriya", "config", "default_config.yaml")
    if os.path.exists(ref_path):
        with open(ref_path, "r", encoding="utf-8") as f:
            ref_data = yaml.safe_load(f) or {}
        expected_temp = ref_data.get("llm", {}).get("temperature")
        if expected_temp is not None:
            assert cfg.llm.temperature == expected_temp
            return
    assert isinstance(cfg.llm.temperature, float)
    assert cfg.paths.skills.endswith("skills")

def test_load_invalid_config(tmp_path):
    invalid_yaml = {
        "llm": {
            "temperature": "not-a-float"
        }
    }
    
    config_file = tmp_path / "invalid_config.yaml"
    with open(config_file, "w") as f:
        yaml.dump(invalid_yaml, f)
        
    with pytest.raises(Exception):
        load_config(str(config_file))
