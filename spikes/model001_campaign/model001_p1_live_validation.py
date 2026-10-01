#!/usr/bin/env python3
"""MODEL-001 P1 - live validation of the production capability contract.

USER-OWNED. The agent that wrote this prepares it but does not run it.

Proves, for each of the three MODEL-001-campaign-evidenced models:

    actual model -> production capability contract (kriya.core.model_capabilities)
    -> expected effective profile (matches KNOWN_MODEL_PROFILES / campaign evidence)
    -> a real capability-dependent interaction succeeds (a genuine native
       tool-calling completion through the real, unmodified LLMClient)

This is NOT a rerun of the 9-arm MODEL-001 campaign (spikes/model001_campaign/
run_harness.py and friends) - it is the smallest live check that the NEW
production contract (kriya/core/model_capabilities.py) actually resolves
correctly against real models, going through the real production
LLMClient.complete_with_tools() call path, not a raw HTTP probe like
probe_capabilities.py used.

Exits non-zero on ANY mismatch (resolution disagrees with the expected
campaign-evidenced profile) or ANY interaction failure. Writes a concise
JSON evidence file next to itself.
"""
import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

KRIYA_REPO = "/Users/sriramnanduri/WorkingDirectory/AI/ClaudeCode/Kriya-By-ClaudeCode"
sys.path.insert(0, KRIYA_REPO)

EVIDENCE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "model001_p1_live_validation_evidence.json")

MODELS = [
    "qwen3-coder:30b",
    "qwen3.6:35b-a3b-q4_K_M",
    "qwen3.5:9B",
]


def _preflight_git_state():
    head = subprocess.run(
        ["git", "-C", KRIYA_REPO, "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
    ).stdout.strip()
    status = subprocess.run(
        ["git", "-C", KRIYA_REPO, "status", "--porcelain", "--untracked-files=no"],
        capture_output=True, text=True, check=True,
    ).stdout
    clean = status.strip() == ""
    return head, clean


def _preflight_models_installed():
    result = subprocess.run(["ollama", "list"], capture_output=True, text=True, check=True)
    installed = result.stdout.lower()
    # A simple substring containment check - the real, decisive check is the
    # live completion call below; this is just a fast fail-fast pre-check.
    return [m for m in MODELS if m.lower() not in installed]


def _build_config_for_primary(model: str):
    from kriya.config import AppConfig

    cfg = AppConfig()
    cfg.llm.model = model
    cfg.llm.base_url = "http://localhost:11434/v1"
    cfg.llm.temperature = 0.2
    # Capabilities deliberately left untouched (bare defaults) - this is
    # exactly the "known production profile, no explicit override" case the
    # KNOWN_MODEL_PROFILES registry exists for. Do NOT set cfg.llm.capabilities
    # here; that would test the "explicit override" tier, not the production
    # registry this validation is actually meant to prove.
    return cfg


async def _validate_one_model(model: str) -> dict:
    from kriya.core.llm import LLMClient
    from kriya.core.model_capabilities import (
        KNOWN_MODEL_PROFILES,
        ModelCapabilityError,
        _normalize_model_identity,
        resolve_model_capability_profile,
    )

    record = {"model": model}

    cfg = _build_config_for_primary(model)
    resolved = resolve_model_capability_profile(cfg, model)
    expected = KNOWN_MODEL_PROFILES.get(_normalize_model_identity(model))

    record["resolved_source"] = resolved.source
    record["resolved_capabilities"] = resolved.capabilities.model_dump()
    record["expected_capabilities"] = expected.model_dump() if expected is not None else None

    profile_matches = (
        expected is not None
        and resolved.source == "known_production_profile"
        and resolved.capabilities == expected
    )
    record["profile_matches_campaign_evidence"] = profile_matches

    if not profile_matches:
        record["interaction"] = "skipped - profile mismatch, not attempting a live call against an unverified resolution"
        record["passed"] = False
        return record

    llm = LLMClient(cfg)
    tools = [{
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the current weather for a location",
            "parameters": {
                "type": "object",
                "properties": {"location": {"type": "string"}},
                "required": ["location"],
            },
        },
    }]
    try:
        result = await llm.complete_with_tools(
            [{"role": "user", "content": "What's the weather in Boston? Use the get_weather tool."}],
            tools,
        )
        tool_calls = result.get("tool_calls") or []
        interaction_ok = len(tool_calls) > 0 and tool_calls[0].get("name") == "get_weather" and not tool_calls[0].get("argument_error")
        record["interaction"] = "native tool call succeeded" if interaction_ok else f"native tool call did not produce the expected shape: {result!r}"
        record["passed"] = interaction_ok
    except ModelCapabilityError as e:
        record["interaction"] = f"ModelCapabilityError (unexpected - resolution said native_tool_calls=True): {e}"
        record["passed"] = False
    except Exception as e:  # noqa: BLE001 - record any live-call failure as evidence, never crash the whole validation
        record["interaction"] = f"{type(e).__name__}: {e}"
        record["passed"] = False

    return record


async def _main_async():
    results = [await _validate_one_model(m) for m in MODELS]
    return results


def main():
    print("=== MODEL-001 P1 live validation ===")

    head, clean = _preflight_git_state()
    print(f"Kriya HEAD: {head}")
    print(f"Worktree clean (tracked): {clean}")
    if not clean:
        print("FATAL: tracked worktree is not clean - commit or stash before running this validation.", file=sys.stderr)
        sys.exit(2)

    missing = _preflight_models_installed()
    if missing:
        print(f"FATAL: required model(s) not found in `ollama list`: {missing}", file=sys.stderr)
        sys.exit(2)
    print(f"Required models present: {MODELS}")

    print("\nRunning live capability-resolution + real tool-call interaction per model...")
    results = asyncio.run(_main_async())

    print("\n--- Results ---")
    all_passed = True
    for r in results:
        status = "PASS" if r["passed"] else "FAIL"
        if not r["passed"]:
            all_passed = False
        print(f"[{status}] {r['model']}: source={r['resolved_source']} matches_evidence={r['profile_matches_campaign_evidence']} interaction={r['interaction']}")

    evidence = {
        "validated_at_utc": datetime.now(timezone.utc).isoformat(),
        "kriya_head": head,
        "worktree_clean": clean,
        "models": MODELS,
        "results": results,
        "all_passed": all_passed,
    }
    with open(EVIDENCE_PATH, "w") as f:
        json.dump(evidence, f, indent=2)
    print(f"\nEvidence written to: {EVIDENCE_PATH}")

    if not all_passed:
        print("\nMODEL-001 P1 LIVE VALIDATION: FAILED - see per-model detail above.", file=sys.stderr)
        sys.exit(1)

    print("\nMODEL-001 P1 LIVE VALIDATION: PASSED - production contract resolved and exercised correctly for all 3 evidenced models.")
    sys.exit(0)


if __name__ == "__main__":
    main()
