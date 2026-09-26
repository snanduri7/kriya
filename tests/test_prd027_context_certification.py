"""PRD-027: context-recall certification contract.

Covers:
- scoring, with every miss reason typed;
- precision;
- fixed class targets;
- the deterministic CI suite: mechanics only, which never satisfy production;
- no model calls;
- stored-record status as doctor reads it (every branch);
- storage outside the workspace;
- the ``kriya context certify`` CLI.
"""
import asyncio
import json
import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from kriya.config import AppConfig
from kriya.workflow import context_certification as cc
from kriya.workflow.context_recall_fixtures import CONTEXT_CLASSES, GoldenItem, RecallCase

# --- scoring -----------------------------------------------------------------------------


def _package(shown, omitted=()):
    return SimpleNamespace(
        relevant_files=[SimpleNamespace(path=path, tier=tier) for path, tier in shown],
        omitted=[{"path": path, "reason": reason} for path, reason in omitted],
    )


_CASE = RecallCase(
    name="c", goal="g",
    golden=(
        GoldenItem("hit.py", "same_class_member", "full"),
        GoldenItem("thin.py", "direct_caller", "skeleton"),
        GoldenItem("cut.py", "one_hop_dependency", "signatures"),
        GoldenItem("gone.py", "two_hop_dependency", "signatures"),
        GoldenItem("never.py", "configuration", "full"),
    ),
    acceptable=("ok.py",),
)


def test_every_golden_item_gets_a_typed_outcome():
    package = _package(
        [("hit.py", "full"), ("thin.py", "signatures"), ("ok.py", "skeleton"), ("noise.py", "full")],
        [("cut.py", "budget_exhausted"), ("gone.py", "source_unavailable"), ("thin.py", "body_elided")],
    )
    result = cc.score_case("repo", _CASE, package, [])
    outcomes = {item.path: item.outcome for item in result.items}
    assert outcomes == {
        "hit.py": cc.HIT, "thin.py": cc.TIER_INSUFFICIENT, "cut.py": cc.BUDGET_EXHAUSTED,
        "gone.py": cc.SOURCE_UNAVAILABLE, "never.py": cc.NOT_RETRIEVED,
    }
    # 4 packaged files; hit/thin are golden, ok is acceptable, noise is not.
    assert result.precision == pytest.approx(3 / 4)


def test_empty_package_scores_every_item_not_retrieved_with_zero_precision():
    result = cc.score_case("repo", _CASE, None, [])
    assert {item.outcome for item in result.items} == {cc.NOT_RETRIEVED}
    assert result.precision == 0.0


def test_targets_are_fixed_version_controlled_and_cover_every_class():
    assert set(cc.CLASS_RECALL_TARGETS) == set(CONTEXT_CLASSES)
    assert cc.CLASS_RECALL_TARGETS == {
        "same_class_member": 1.0, "interface_contract": 1.0, "direct_caller": 1.0, "one_hop_dependency": 1.0,
        "sibling_implementation": 0.5, "two_hop_dependency": 0.5, "test_precedent": 0.5,
        "build_metadata": 0.5, "configuration": 0.5,
    }
    assert cc.PRECISION_TARGET == 0.5


def test_indiscriminate_retrieval_cannot_certify():
    report = cc.CertificationReport(identity={})
    everything = [(f"f{i}.py", "full") for i in range(20)] + [("hit.py", "full")]
    case = RecallCase(name="c", goal="g", golden=(GoldenItem("hit.py", "same_class_member", "full"),))
    report.cases.append(cc.score_case("repo", case, _package(everything), []))
    assert report.class_recall()["same_class_member"]["passed"] is True
    assert report.precision() < cc.PRECISION_TARGET
    assert report.certified() is False


# --- the deterministic suite -----------------------------------------------------------


def _deterministic_report(config=None):
    return asyncio.run(cc.run_certification(
        config or AppConfig(), embedding_client=cc.DeterministicHashingEmbedder(),
        embedder=cc.EMBEDDER_DETERMINISTIC, embedding_runtime="n/a",
    ))


def test_deterministic_suite_meets_every_target_without_any_model_call():
    with patch("kriya.core.llm.LLMClient.complete", side_effect=AssertionError("no model call")), \
         patch("kriya.core.llm.LLMClient.complete_result", side_effect=AssertionError("no model call")):
        report = _deterministic_report()
    data = report.to_dict()
    assert data["identity"]["embedder"] == cc.EMBEDDER_DETERMINISTIC
    failing = {name: entry for name, entry in data["classes"].items() if not entry["passed"]}
    assert failing == {}, failing
    assert data["precision"] >= cc.PRECISION_TARGET
    assert data["certified"] is True
    for entry in data["classes"].values():
        assert entry["golden"] >= 2


def test_deterministic_suite_is_reproducible():
    first, second = _deterministic_report().to_dict(), _deterministic_report().to_dict()
    assert first == second


def test_certification_covers_every_production_retrieval_policy():
    config = AppConfig()
    assert cc.retrieval_policies(config) == (cc.DEFAULT_RETRIEVAL_LIMITS,)
    config.process_profiles.enabled = True
    config.process_profiles.enforce_context_depth = True
    assert len(cc.retrieval_policies(config)) == 3


# --- stored record status (what doctor reads) --------------------------------------------


@pytest.fixture
def indexed_config(tmp_path):
    config = AppConfig()
    config.paths.memory = str(tmp_path / "memory")
    os.makedirs(config.paths.memory)
    open(os.path.join(config.paths.memory, "vector_index.db"), "wb").close()
    return config


def test_status_not_applicable_without_a_code_index(tmp_path):
    config = AppConfig()
    config.paths.memory = str(tmp_path / "none")
    assert cc.certification_status(config)[0] == cc.STATUS_NOT_APPLICABLE


def test_status_unavailable_when_the_embedding_runtime_cannot_be_proven(indexed_config):
    # conftest disables runtime probing: nothing can be proven exact.
    assert cc.certification_status(indexed_config)[0] == cc.STATUS_UNAVAILABLE


def _save(config, *, certified, runtime="rt-1", embedder=cc.EMBEDDER_CONFIGURED):
    report = cc.CertificationReport(identity=cc.certification_identity(
        config, embedder=embedder, embedding_runtime=runtime,
    ))
    with patch.object(cc.CertificationReport, "certified", return_value=certified):
        return cc.save_certification(config, report)


def test_status_transitions(indexed_config, monkeypatch):
    monkeypatch.setattr(cc, "embedding_runtime_identity", lambda config: "rt-1")
    assert cc.certification_status(indexed_config)[0] == cc.STATUS_MISSING

    path = _save(indexed_config, certified=False)
    status, detail = cc.certification_status(indexed_config)
    assert status == cc.STATUS_FAILED and "not passing" in detail

    _save(indexed_config, certified=True)
    assert cc.certification_status(indexed_config)[0] == cc.STATUS_CERTIFIED

    with open(path, "w", encoding="utf-8") as handle:
        handle.write("{not json")
    assert cc.certification_status(indexed_config)[0] == cc.STATUS_FAILED


def test_a_deterministic_report_never_satisfies_production(indexed_config, monkeypatch):
    monkeypatch.setattr(cc, "embedding_runtime_identity", lambda config: "rt-1")
    _save(indexed_config, certified=True, embedder=cc.EMBEDDER_DETERMINISTIC)
    assert cc.certification_status(indexed_config)[0] == cc.STATUS_MISSING


def test_a_different_embedding_runtime_is_a_stale_certification(indexed_config, monkeypatch):
    _save(indexed_config, certified=True, runtime="rt-old")
    monkeypatch.setattr(cc, "embedding_runtime_identity", lambda config: "rt-new")
    assert cc.certification_status(indexed_config)[0] == cc.STATUS_MISSING


def test_record_is_stored_under_the_state_directory_atomically(indexed_config, tmp_path):
    path = _save(indexed_config, certified=True)
    assert path.startswith(os.environ["KRIYA_STATE_DIR"])
    assert not os.path.exists(f"{path}.tmp")
    with open(path, encoding="utf-8") as handle:
        record = json.load(handle)
    assert record["record_version"] == cc.CERTIFICATION_RECORD_VERSION
    assert record["identity_digest"] == os.path.basename(path)[:-len(".json")]
    assert record["recorded_at"]


# --- CLI ----------------------------------------------------------------------------------


def _config_file(tmp_path):
    path = tmp_path / "operator.yaml"
    path.write_text("{}\n", encoding="utf-8")
    return str(path)


def test_cli_refuses_to_certify_an_unproven_embedding_runtime(tmp_path):
    from kriya.cli import main

    result = CliRunner().invoke(main, ["--config", _config_file(tmp_path), "context", "certify"])
    assert result.exit_code == 1
    assert "cannot be proven" in result.output


def test_cli_certifies_and_records(tmp_path, monkeypatch):
    from kriya.cli import main

    monkeypatch.setattr(cc, "embedding_runtime_identity", lambda config: "rt-1")
    with patch("kriya.memory.vector.OllamaEmbeddingClient", lambda **kw: cc.DeterministicHashingEmbedder()):
        result = CliRunner().invoke(main, ["--config", _config_file(tmp_path), "context", "certify", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output[result.output.index("{"):])
    assert data["certified"] is True
    assert data["identity"]["embedder"] == cc.EMBEDDER_CONFIGURED
    assert os.path.exists(data["record_path"])


def test_precision_below_target_blocks_even_when_every_class_recalls():
    report = cc.CertificationReport(identity={})
    golden = tuple(GoldenItem(f"{name}.py", name, "signatures") for name in CONTEXT_CLASSES)
    noise = [(f"noise{i}.py", "full") for i in range(40)]
    case = RecallCase(name="all", goal="g", golden=golden)
    report.cases.append(cc.score_case("repo", case, _package([(item.path, "full") for item in golden] + noise), []))
    assert all(entry["passed"] for entry in report.class_recall().values())
    assert report.precision() < cc.PRECISION_TARGET
    assert report.certified() is False


def test_a_record_whose_identity_does_not_match_its_key_is_rejected(indexed_config, monkeypatch):
    monkeypatch.setattr(cc, "embedding_runtime_identity", lambda config: "rt-1")
    path = _save(indexed_config, certified=True)
    with open(path, encoding="utf-8") as handle:
        record = json.load(handle)
    record["identity"]["embedding_runtime"] = "rt-other"
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(record, handle)
    assert cc.certification_status(indexed_config)[0] == cc.STATUS_FAILED


def test_cli_exits_nonzero_when_the_suite_does_not_certify(tmp_path, monkeypatch):
    from kriya.cli import main

    monkeypatch.setattr(cc, "embedding_runtime_identity", lambda config: "rt-1")
    monkeypatch.setattr(cc.CertificationReport, "certified", lambda self: False)
    with patch("kriya.memory.vector.OllamaEmbeddingClient", lambda **kw: cc.DeterministicHashingEmbedder()):
        result = CliRunner().invoke(main, ["--config", _config_file(tmp_path), "context", "certify"])
    assert result.exit_code == 1
    assert "CERTIFIED=false" in result.output


def test_an_embedding_model_runtime_can_be_proven_exact(monkeypatch):
    """Doctor-blocker guard: `kriya context certify` must be able to bind to
    a real embedding runtime. Drive the real PRD-013 probe with an
    embedding-model-shaped Ollama endpoint."""
    from kriya.core import model_runtime

    def transport(url, payload, api_key):
        if url.endswith("/api/version"):
            return {"version": "0.34.2"}
        if url.endswith("/api/tags"):
            return {"models": [{"name": "nomic-embed-text:latest", "digest": "0a109f422b47"}]}
        return {"details": {"family": "nomic-bert", "format": "gguf"}, "model_info": {},
                "modelfile": "FROM /blobs/sha256-970aa74c0a90", "capabilities": ["embedding"]}

    monkeypatch.setenv(model_runtime.PROBE_ENV_VAR, "1")
    monkeypatch.setattr(model_runtime, "_http_json", transport)
    model_runtime.clear_model_runtime_cache()
    try:
        config = AppConfig()
        identity = cc.embedding_runtime_identity(config)
    finally:
        model_runtime.clear_model_runtime_cache()
    assert identity != "unavailable"
    assert len(identity) == 64
