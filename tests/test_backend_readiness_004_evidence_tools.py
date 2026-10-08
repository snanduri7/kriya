"""BACKEND-READINESS-004: the hidden-oracle leak check (owner section 12) and the rejected-candidate export
(REJECTED-CANDIDATE-RETENTION-001) - both read a sealed attempt-evidence store written by the real writer.

Leak check: blob-level, on the actual model-facing bytes; hidden-only units (lines absent from the public file) and
markers; CLEAN needs a positive control (a public line found in a blob) or the verdict is INCONCLUSIVE; a hidden
line in a prompt is LEAKED; digest names and record kinds are never proof.
Export: the STAGED candidate.change blobs (after/before/diff) are reconstructed byte-exactly under an operator
directory outside the workspace; a directory inside the workspace is refused; digests-only capture is reported.
"""
import hashlib
import json

import pytest
from click.testing import CliRunner

from kriya.core.attempt_evidence import leak_check as lc
from kriya.core.attempt_evidence import model
from kriya.core.attempt_evidence.candidate_export import EXPORT_MANIFEST, export_candidate
from kriya.core.attempt_evidence.writer import AttemptEvidenceWriter

HIDDEN = ("import pytest\n\n\ndef test_ttl_boundary():\n    cache = TTLCache(maxsize=3, ttl=3, timer=Timer())\n"
          "    assert cache.expire(3) == [(1, 1)]\n    assert len(cache) == 2  # HIDDEN_ONLY_ASSERTION\n")
PUBLIC = "import pytest\n\n\ndef test_ttl_basic():\n    cache = TTLCache(maxsize=3, ttl=3, timer=Timer())\n    assert cache.currsize == 0\n"
BEFORE = b"dependencies = []\n"
AFTER = b'dependencies = ["text-unidecode>=1.3"]\n'
DIFF = b"--- a/pyproject.toml\n+++ b/pyproject.toml\n@@ -1 +1 @@\n-dependencies = []\n+dependencies = [\"text-unidecode>=1.3\"]\n"


def _store(tmp_path, run_id, *, prompt_text, capture=model.CAPTURE_FULL, staged=True):
    state = str(tmp_path / "state")
    writer = AttemptEvidenceWriter(state, run_id, capture=capture, manifest={"goal_digest": "g" * 64})
    writer.append("run.opened", {"goal_digest": "g" * 64})
    writer.append("model.request", {"role": "developer", "messages_digest": "x"},
                  content={"messages": [{"role": "user", "content": prompt_text}]},
                  identity={"attempt_number": 1})
    if staged:
        writer.append("candidate.change", {"decision": "STAGED", "path": "pyproject.toml", "created": False, "deleted": False,
                                           "after_digest": hashlib.sha256(AFTER).hexdigest(),
                                           "before_digest": hashlib.sha256(BEFORE).hexdigest(), "text_diff": True},
                      content={"before": BEFORE, "after": AFTER, "diff": DIFF}, identity={"attempt_number": 1})
    writer.seal("closed")
    return state


def test_01_clean_needs_a_positive_control_and_a_hidden_line_in_a_prompt_is_a_leak(tmp_path):
    state = _store(tmp_path, "run-clean", prompt_text="Workspace file tests/test_ttl.py:\n" + PUBLIC)
    report = lc.check_run(state, "run-clean", hidden_texts=[HIDDEN], public_texts=[PUBLIC])
    assert report.verdict == lc.CLEAN and report.hits == [] and report.model_facing_blobs == 1
    assert report.hidden_only_units == 3 and report.positive_control["found"] >= 1
    # a hidden file sharing nothing with the prompt and no public counterpart: no hit, but nothing proves the
    # blobs carry workspace content either -> INCONCLUSIVE, never CLEAN
    inconclusive = lc.check_run(state, "run-clean", hidden_texts=["def test_secret_case():\n    assert secret() == 42\n"], public_texts=[])
    assert inconclusive.verdict == lc.INCONCLUSIVE and inconclusive.hits == []
    # the same hidden file with NO public counterpart named: its shared lines look like leaks - the operator must
    # name the public files (the T1 lesson: identifiers shared with the workspace legitimately appear)
    assert lc.check_run(state, "run-clean", hidden_texts=[HIDDEN], public_texts=[]).verdict == lc.LEAKED
    # a prompt that carries a hidden-only line, or a marker, is LEAKED - whatever the record is named
    leaked_state = _store(tmp_path / "l", "run-leak", prompt_text=PUBLIC + "\n    assert len(cache) == 2  # HIDDEN_ONLY_ASSERTION\n")
    leaked = lc.check_run(leaked_state, "run-leak", hidden_texts=[HIDDEN], public_texts=[PUBLIC])
    assert leaked.verdict == lc.LEAKED and leaked.hits[0]["what"] == "hidden-only unit" and "HIDDEN_ONLY" in leaked.hits[0]["unit"]
    marker_state = _store(tmp_path / "m", "run-marker", prompt_text=PUBLIC + "\nsee .kriya/authority/verify.sh\n")
    marked = lc.check_run(marker_state, "run-marker", hidden_texts=[HIDDEN], public_texts=[PUBLIC])
    assert marked.verdict == lc.LEAKED and {h["what"] for h in marked.hits} == {"marker"}
    # a store without blobs can prove nothing: UNAVAILABLE, never CLEAN
    digests = _store(tmp_path / "d", "run-digests", prompt_text=PUBLIC, capture=model.CAPTURE_DIGEST_ONLY)
    assert lc.check_run(digests, "run-digests", hidden_texts=[HIDDEN], public_texts=[PUBLIC]).verdict == lc.UNAVAILABLE
    assert lc.check_run(state, "no-such-run", hidden_texts=[HIDDEN]).verdict == lc.UNAVAILABLE


def test_02_hidden_only_units_exclude_what_the_public_file_shares_and_trivial_lines():
    units = lc.hidden_only_units([HIDDEN], [PUBLIC])
    assert "import pytest" not in units and "cache = TTLCache(maxsize=3, ttl=3, timer=Timer())" not in units
    assert units == ["def test_ttl_boundary():", "assert cache.expire(3) == [(1, 1)]",
                     "assert len(cache) == 2  # HIDDEN_ONLY_ASSERTION"]
    assert lc._units("x = 1\n{\n}\npass\n") == []  # nothing short or symbol-only counts


def test_03_a_rejected_candidate_is_reconstructed_byte_exactly_outside_the_workspace(tmp_path):
    state = _store(tmp_path, "run-rej", prompt_text=PUBLIC)
    out = tmp_path / "export"
    manifest = export_candidate(state, "run-rej", str(out), workspace=str(tmp_path / "ws"))
    assert manifest["attempt"] == 1 and [p["path"] for p in manifest["paths"]] == ["pyproject.toml"]
    assert (out / "pyproject.toml").read_bytes() == AFTER and (out / "diffs" / "pyproject.toml.diff").read_bytes() == DIFF
    written = json.load(open(out / EXPORT_MANIFEST))
    assert written["paths"][0]["after_digest"] == hashlib.sha256(AFTER).hexdigest() and written["store_verification"]
    # never into the workspace
    ws = tmp_path / "ws"
    ws.mkdir()
    with pytest.raises(ValueError, match="outside the workspace"):
        export_candidate(state, "run-rej", str(ws / "candidate"), workspace=str(ws))
    assert not (ws / "candidate").exists()
    # digests-only capture: reported, not invented; no staged candidate: said so
    digests = _store(tmp_path / "d", "run-digests", prompt_text=PUBLIC, capture=model.CAPTURE_DIGEST_ONLY)
    only = export_candidate(digests, "run-digests", str(tmp_path / "d-out"))
    assert only["paths"] == [] and only["without_bytes"][0]["problem"].startswith("no after-bytes blob")
    none = _store(tmp_path / "n", "run-none", prompt_text=PUBLIC, staged=False)
    assert export_candidate(none, "run-none", str(tmp_path / "n-out"))["reason"].startswith("no staged candidate")
    with pytest.raises(FileNotFoundError):
        export_candidate(state, "missing", str(tmp_path / "x"))


def test_04_the_cli_commands_exit_by_verdict(tmp_path, monkeypatch):
    from unittest.mock import patch

    from kriya.config.config import AppConfig

    state = _store(tmp_path, "run-cli", prompt_text="Workspace file tests/test_ttl.py:\n" + PUBLIC)
    hidden = tmp_path / "hidden.py"
    hidden.write_text(HIDDEN)
    public = tmp_path / "public.py"
    public.write_text(PUBLIC)
    monkeypatch.setenv("KRIYA_STATE_DIR", state)
    (tmp_path / "ws").mkdir()
    monkeypatch.chdir(tmp_path / "ws")  # the workspace: the export must land outside it
    cfg = AppConfig()
    cfg.paths.state = state
    main = __import__("kriya.cli", fromlist=["main"]).main
    with patch("kriya.cli.load_config", return_value=cfg):
        clean = CliRunner().invoke(main, ["evidence", "leak-check", "run-cli", "--hidden", str(hidden), "--public", str(public)])
        assert clean.exit_code == 0 and clean.output.startswith("CLEAN:"), clean.output
        secret = tmp_path / "secret.py"
        secret.write_text("def test_secret_case():\n    assert secret() == 42\n")
        inconclusive = CliRunner().invoke(main, ["evidence", "leak-check", "run-cli", "--hidden", str(secret), "--json"])
        assert inconclusive.exit_code == 1 and json.loads(inconclusive.output)["verdict"] == "INCONCLUSIVE"
        exported = CliRunner().invoke(main, ["evidence", "candidate", "run-cli", "--out", str(tmp_path / "out"), "--json"])
        assert exported.exit_code == 0, exported.output
        assert json.loads(exported.output)["paths"][0]["path"] == "pyproject.toml"
        assert (tmp_path / "out" / "pyproject.toml").read_bytes() == AFTER
        inside = CliRunner().invoke(main, ["evidence", "candidate", "run-cli", "--out", str(tmp_path / "ws" / "cand")])
        assert inside.exit_code == 2 and "outside the workspace" in inside.output and not (tmp_path / "ws" / "cand").exists()
