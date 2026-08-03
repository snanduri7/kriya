"""Tests for kriya/tools/lsp.py's hand-rolled jdtls client - fully mocked,
no real jdtls process, matching this repo's existing testing philosophy.
Uses a small in-memory fake stdin/stdout to validate the actual Content-
Length framing and request/notification handling logic, not just the
public API shape."""
import asyncio
import json
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kriya.tools.lsp import (
    JdtlsClient,
    find_jdtls,
    format_diagnostics_for_prompt,
)


def _frame(message: Dict[str, Any]) -> bytes:
    body = json.dumps(message).encode("utf-8")
    return f"Content-Length: {len(body)}\r\n\r\n".encode("utf-8") + body


class _FakeStdout:
    """Serves a pre-canned sequence of Content-Length-framed LSP messages,
    line-by-line for headers then exact-byte-count for the body - matching
    real asyncio StreamReader semantics closely enough for _read_message(),
    including BLOCKING (not EOF) when the buffer is temporarily empty but
    more data may still arrive - a real stream doesn't signal EOF just
    because nothing is queued yet. More messages can be pushed later via
    append(), simulating a server's asynchronous notification after a
    request was sent, distinct from close() (real EOF/process exit)."""
    def __init__(self, messages: List[Dict[str, Any]]):
        self._buffer = b"".join(_frame(m) for m in messages)
        self._new_data = asyncio.Event()
        self._new_data.set()
        self._closed = False

    def append(self, message: Dict[str, Any]) -> None:
        self._buffer += _frame(message)
        self._new_data.set()

    def close(self) -> None:
        self._closed = True
        self._new_data.set()

    async def readline(self) -> bytes:
        while True:
            idx = self._buffer.find(b"\n")
            if idx != -1:
                line, self._buffer = self._buffer[:idx + 1], self._buffer[idx + 1:]
                return line
            if self._closed:
                line, self._buffer = self._buffer, b""
                return line
            self._new_data.clear()
            await self._new_data.wait()

    async def readexactly(self, n: int) -> bytes:
        while len(self._buffer) < n:
            if self._closed:
                raise asyncio.IncompleteReadError(self._buffer, n)
            self._new_data.clear()
            await self._new_data.wait()
        data, self._buffer = self._buffer[:n], self._buffer[n:]
        return data


class _FakeStdin:
    def __init__(self):
        self.written = b""

    def write(self, data: bytes) -> None:
        self.written += data

    async def drain(self) -> None:
        pass


def _make_client(messages: List[Dict[str, Any]]) -> JdtlsClient:
    client = JdtlsClient("/fake/project", "/fake/jdtls")
    client.process = MagicMock()
    client.process.stdin = _FakeStdin()
    client.process.stdout = _FakeStdout(messages)
    return client


def test_find_jdtls_returns_none_when_not_on_path():
    with patch("shutil.which", return_value=None):
        assert find_jdtls() is None


def test_find_jdtls_returns_path_when_found():
    with patch("shutil.which", return_value="/opt/homebrew/bin/jdtls"):
        assert find_jdtls() == "/opt/homebrew/bin/jdtls"


@pytest.mark.asyncio
async def test_read_message_parses_framed_json():
    client = _make_client([{"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}])
    message = await client._read_message()
    assert message == {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}


@pytest.mark.asyncio
async def test_write_message_uses_correct_content_length_framing():
    client = _make_client([])
    client._write_message({"jsonrpc": "2.0", "method": "initialized", "params": {}})
    written = client.process.stdin.written.decode("utf-8")
    header, _, body = written.partition("\r\n\r\n")
    assert header == f"Content-Length: {len(body.encode())}"
    assert json.loads(body) == {"jsonrpc": "2.0", "method": "initialized", "params": {}}


@pytest.mark.asyncio
async def test_check_file_sends_did_open_on_first_call_then_did_change():
    client = _make_client([])
    client._diagnostics["file:///App.java"] = []  # pre-populate so polling returns immediately

    await client.check_file("/App.java", "class App {}", timeout=1)
    first_write = client.process.stdin.written.decode("utf-8")
    assert '"method": "textDocument/didOpen"' in first_write or "textDocument/didOpen" in first_write

    client.process.stdin.written = b""
    await client.check_file("/App.java", "class App { int x; }", timeout=1)
    second_write = client.process.stdin.written.decode("utf-8")
    assert "textDocument/didChange" in second_write
    assert '"version": 2' in second_write

@pytest.mark.asyncio
async def test_check_file_returns_empty_on_timeout_without_raising():
    client = _make_client([])
    # No diagnostics ever arrive - must degrade to empty, not hang or raise.
    result = await client.check_file("/App.java", "class App {}", timeout=0.3)
    assert result == []

@pytest.mark.asyncio
async def test_start_and_check_file_end_to_end_via_fake_process():
    """Full round trip through the real read loop: initialize's response
    unblocks start(), then a publishDiagnostics notification pushed AFTER
    check_file() sends its didOpen (matching how a real server behaves -
    diagnostics arrive asynchronously in response to the edit, not before
    it) is what check_file() actually returns - not a mocked shortcut, the
    real _read_loop()/_read_message() parsing path."""
    diagnostics_payload = [{
        "range": {"start": {"line": 4, "character": 0}, "end": {"line": 4, "character": 10}},
        "severity": 1,
        "message": "The import org.apache.ignite.cache.IgniteCache cannot be resolved",
    }]
    init_response = {"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}}
    client = _make_client([init_response])

    async def push_diagnostics_after_delay():
        await asyncio.sleep(0.1)
        client.process.stdout.append({
            "jsonrpc": "2.0",
            "method": "textDocument/publishDiagnostics",
            "params": {"uri": "file:///IntegrationApp.java", "diagnostics": diagnostics_payload},
        })

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=client.process)), \
         patch("tempfile.mkdtemp", return_value="/tmp/fake-jdtls-data"), \
         patch("shutil.rmtree"):
        await client.start()
        push_task = asyncio.create_task(push_diagnostics_after_delay())
        result = await client.check_file("/IntegrationApp.java", "class IntegrationApp {}", timeout=2)
        await push_task
        client.process.stdout.close()
        client._reader_task.cancel()  # skip the real shutdown handshake - not what this test covers

    assert result == diagnostics_payload

@pytest.mark.asyncio
async def test_start_does_not_inherit_java_home_from_parent_process():
    """Regression test for a real bug found live, not theoretical: jdtls's own
    launcher refuses to start ("Exception: jdtls requires at least Java 21")
    if a JAVA_HOME inherited from the parent process resolves to anything
    older - and Kriya's own Maven-oriented workflows routinely export a
    JAVA_HOME pinned to an older JDK for the TARGET project's own reasons
    (e.g. Java 17 for the Qpid Security Manager requirement), unrelated to
    what jdtls itself needs to run. Confirmed live: with JAVA_HOME set to a
    real Temurin 17 install, `jdtls -data ...` crashed immediately with that
    exact exception; with JAVA_HOME unset, it started and ran normally.
    Exercises the real start() method, not a hand-copied duplicate of it."""
    init_response = {"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}}
    client = _make_client([init_response])
    with patch.dict("os.environ", {"JAVA_HOME": "/Library/Java/JavaVirtualMachines/temurin-17.jdk/Contents/Home", "PATH": "/usr/bin"}), \
         patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=client.process)) as mock_exec, \
         patch("tempfile.mkdtemp", return_value="/tmp/fake-jdtls-data"):
        await client.start()

    launch_kwargs = mock_exec.call_args.kwargs
    assert "JAVA_HOME" not in launch_kwargs["env"]
    assert launch_kwargs["env"]["PATH"] == "/usr/bin"  # everything else still passed through

def test_format_diagnostics_for_prompt_filters_to_errors_only():
    diagnostics = [
        {"severity": 1, "range": {"start": {"line": 4}}, "message": "cannot resolve import"},
        {"severity": 2, "range": {"start": {"line": 10}}, "message": "unused variable"},
    ]
    result = format_diagnostics_for_prompt("IntegrationApp.java", diagnostics)
    assert "cannot resolve import" in result
    assert "unused variable" not in result
    assert "[line 5]" in result  # 0-indexed LSP line -> 1-indexed for humans

def test_format_diagnostics_for_prompt_empty_when_no_errors():
    diagnostics = [{"severity": 2, "range": {"start": {"line": 0}}, "message": "unused import"}]
    assert format_diagnostics_for_prompt("App.java", diagnostics) == ""

def test_format_diagnostics_for_prompt_empty_on_no_diagnostics():
    assert format_diagnostics_for_prompt("App.java", []) == ""

def test_format_diagnostics_for_prompt_is_forceful_ground_truth_framing():
    # The user explicitly asked for this to compel a fix, not read as a
    # passive suggestion - confirm the framing is actually present, not just
    # the raw diagnostic text.
    diagnostics = [{"severity": 1, "range": {"start": {"line": 0}}, "message": "cannot resolve import IgniteCache"}]
    result = format_diagnostics_for_prompt("App.java", diagnostics)
    assert "ground truth" in result.lower()
    assert "not a guess" in result.lower()
    assert "same error WILL happen again" in result
