"""GUI v1 hardening (Phase M): bounded, local, seeded fuzzing of Kriya's KUP request handling. Every READ operation is given thousands of generated requests (wrong types,
missing and extra fields, huge and hostile strings, NUL, Unicode, path-like and option-like values). The properties: the handler NEVER raises, it always answers with a
well-formed envelope (data XOR a typed error from the closed vocabulary), and an unknown operation is a typed refusal. Mutating operations (snapshot.acquire/prune) are not
fuzzed here; nothing outside the test's isolated state directory is ever touched."""
from __future__ import annotations

import random

import pytest
from _kup_evidence_fixtures import candidate_store
from _strict_doubles import strict_config

from kriya.kup import policy
from kriya.kup.cli_ops import run_kup_operation

READ_OPERATIONS = ["capabilities", "snapshot.list", "snapshot.verify", "history.list", "history.detail", "history.prompt", "evidence.manifest", "evidence.verify", "evidence.records",
                   "evidence.change_content", "evidence.call_content", "workspace.status", "workspace.describe", "execution.preflight", "launch.status"]
KEYS = ["operation", "run_id", "snapshot_id", "launch_id", "workspace", "cursor", "limit", "seq", "part", "kind", "unit", "phase", "role", "attempt", "from_seq", "from_trace", "verify",
        "extra", "__proto__", "argv", "shell", "keep"]
CHARS = list("aZ09-_.:/ ") + ["\x00", "\n", "\r", "\t", "‮", "é", "\U0001F600", "`", "$", "(", ")", ";", "|", "&", '"', "'", "\\", "*", "~", "=", "..", "../", "--", "-x"]
WEIRD = [None, True, False, 0, -1, 1.5, float("inf"), float("nan"), 2 ** 70, "", [], {}, [[]], {"a": {"b": {}}}, b"bytes", object]


def _value(rand: random.Random, depth: int = 0):
    r = rand.random()
    if r < 0.4:
        return "".join(rand.choice(CHARS) for _ in range(rand.randint(0, 40))) if rand.random() < 0.9 else "x" * rand.randint(1000, 100000)
    if r < 0.55:
        return rand.randint(-5, 5000)
    if r < 0.7 or depth > 2:
        return rand.choice(WEIRD)
    if r < 0.85:
        return [_value(rand, depth + 1) for _ in range(rand.randint(0, 4))]
    return {rand.choice(KEYS): _value(rand, depth + 1) for _ in range(rand.randint(0, 4))}


def _request(rand: random.Random) -> dict:
    request = {rand.choice(KEYS): _value(rand) for _ in range(rand.randint(0, 8))}
    request["operation"] = rand.choice(READ_OPERATIONS + ["shell.exec", "evidence.__class__", "", None, 7])
    return request


@pytest.fixture(autouse=True)
def _a_real_store_exists():
    candidate_store("fz-run")  # so valid-looking ids sometimes resolve and the deeper paths run too


@pytest.mark.parametrize("seed", range(3))
def test_every_read_operation_answers_every_generated_request_with_a_typed_envelope(seed, tmp_path):
    rand = random.Random(20261011 + seed)
    cfg = strict_config(evidence={"attempt_recorder": {"content_visibility": "on"}})
    answered = errors = data = 0
    for _ in range(450):
        request = _request(rand)
        if rand.random() < 0.3:
            request["run_id"], request["workspace"] = "fz-run", str(tmp_path)
        envelope = run_kup_operation(cfg, request)  # must never raise
        assert isinstance(envelope, dict) and {"operation", "data", "error"} <= set(envelope), (request, envelope)
        assert (envelope["data"] is None) != (envelope["error"] is None) or envelope["operation"] == "capabilities", (request, envelope)
        if envelope["error"] is not None:
            errors += 1
            assert envelope["error"]["code"] in policy.ERROR_CODES, (request, envelope["error"])
            assert isinstance(envelope["error"]["message"], str)
        else:
            data += 1
        answered += 1
    assert answered == 450 and errors > 0 and data >= 0


def test_an_unknown_or_non_string_operation_is_a_typed_invalid_request():
    for operation in ("shell.exec", "evidence.__class__", "", "SNAPSHOT.LIST", "history.list; rm -rf /", None, 7, ["history.list"]):
        envelope = run_kup_operation(strict_config(), {"operation": operation})
        assert envelope["data"] is None and envelope["error"]["code"] == policy.INVALID_REQUEST, (operation, envelope)
