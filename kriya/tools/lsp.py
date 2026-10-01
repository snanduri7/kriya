"""Minimal, hand-rolled Language Server Protocol (LSP) client for jdtls
(Eclipse JDT Language Server) - grounding for the Developer retry loop
against the project's REAL resolved type graph, rather than the model's own
knowledge or a prose skill rule it may still not apply. Java-only for now.

Hand-rolled deliberately, not built on a Python LSP client library: research
(2026-08-03) found no well-maintained library that actually supports
`textDocument/publishDiagnostics` for jdtls specifically (multilspy - the
most prominent candidate - explicitly doesn't implement it). Real production
precedent (OpenCode, a real coding agent) also hand-rolls this exact thing
over plain stdio rather than adopting a dependency - only ~5 message types
are actually needed (initialize/initialized, didOpen/didChange,
publishDiagnostics, shutdown/exit), so a minimal client is genuinely simpler
than integrating a general-purpose one.

Informational only, never gating: a real, corroborated jdtls issue
(eclipse-jdtls/eclipse.jdt.ls#1320) documents it reporting an incomplete
classpath right after a Maven project changes, and it doesn't always
self-heal automatically - a stale index could plausibly produce a false
"unresolved import" right after Kriya adds a new dependency. A fresh temp
data directory per generation run (not a persistent/cached index) trades
re-indexing cost for avoiding that staleness, matching OpenCode's own
validated approach rather than trying to invent something better.
"""
import asyncio
import json
import logging
import os
import shutil
import tempfile
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# jdtls itself needs a modern JVM to RUN - separate from whatever JDK the
# analyzed project targets. Confirmed via multiple independent sources
# (Homebrew's own formula, nvim-jdtls docs, and OpenCode's own source, which
# refuses to start below this) - a real, previously-undocumented-by-Kriya
# pitfall: a tool that assumed "Java 17+" here hit a silent LSP init timeout
# in the wild (anthropics/claude-plugins-official#2616).
JDTLS_MIN_JAVA_MAJOR_VERSION = 21

JDTLS_INIT_TIMEOUT_SECONDS = 120
JDTLS_DIAGNOSTICS_TIMEOUT_SECONDS = 30
# LSP diagnostic severity 1 == Error (2=Warning, 3=Info, 4=Hint) - only errors
# are worth interrupting a retry prompt for; a hint/info-level diagnostic is
# exactly the kind of noise this whole mechanism exists to cut through, not add.
LSP_SEVERITY_ERROR = 1


# Never mirrored into JDTLS's private project copy: VCS data, Kriya's own state,
# build output and IDE metadata (JDTLS regenerates its own inside the mirror).
_MIRROR_SKIP_DIRS = frozenset({".git", ".kriya", "target", "build", ".gradle", "node_modules", ".settings", ".idea"})
_MIRROR_SKIP_FILES = frozenset({".project", ".classpath"})


def find_jdtls() -> Optional[str]:
    """Locates the `jdtls` launcher on PATH. Kriya requires a manual install
    (e.g. `brew install jdtls`), matching how it already treats `mvn`/`java`/
    Ollama - checked via `kriya doctor`, never auto-downloaded. Returns None
    (never raises) if not found, so every caller degrades cleanly to today's
    behavior with zero LSP grounding."""
    return shutil.which("jdtls")


class JdtlsClient:
    """One jdtls process per Developer retry loop (started lazily on first
    real need, kept alive across retries within that run, shut down at run
    end) - not started per-attempt, since indexing can take real time
    (observed up to ~2 minutes on larger codebases), and not shared/cached
    across separate generation runs, to avoid trusting a possibly-stale
    index (see module docstring)."""

    def __init__(self, project_root: str, jdtls_path: str):
        self.project_root = project_root
        self.jdtls_path = jdtls_path
        self.process: Optional[asyncio.subprocess.Process] = None
        self._data_dir: Optional[str] = None
        self._next_id = 1
        self._pending: Dict[int, "asyncio.Future[Any]"] = {}
        self._diagnostics: Dict[str, List[Dict[str, Any]]] = {}
        self._open_docs: Dict[str, int] = {}
        self._reader_task: Optional[asyncio.Task] = None
        # D1 (2026-10-01): JDTLS never sees the candidate tree. Its Maven
        # import writes .project/.classpath/.settings/ and builds target/ into
        # whatever project root it is given (measured with the real 1.60.0,
        # import/autobuild preferences included), so it is rooted at a
        # Kriya-owned private mirror (canonical path) that is synced from the
        # candidate before every query; the candidate is only ever read.
        self._mirror: Optional[str] = None
        self._mirrored: Dict[str, Tuple[int, int]] = {}

    async def start(self) -> None:
        """Launch jdtls and complete the initialize handshake. On ANY failure
        (launch error, initialize timeout, cancellation) everything already
        acquired - the data dir, the process, the reader task - is released
        before the exception propagates: the caller degrades to no LSP
        grounding and never holds the client, so nothing else would ever
        call shutdown() (LEAK-JDTLS-START-FAILURE-001)."""
        try:
            await self._start()
        except BaseException:
            await self._release()
            raise

    async def _start(self) -> None:
        self._data_dir = tempfile.mkdtemp(prefix="kriya-jdtls-data-")
        self._mirror = os.path.realpath(tempfile.mkdtemp(prefix="kriya-jdtls-mirror-"))
        self._sync_mirror()
        # Confirmed live, not theoretical: jdtls's own launcher REFUSES to start
        # ("Exception: jdtls requires at least Java 21") if a JAVA_HOME inherited
        # from the parent process resolves to anything older - and Kriya's own
        # Maven-oriented workflows routinely export a JAVA_HOME pinned to an
        # older JDK for the TARGET project's own reasons (e.g. Java 17 for the
        # Qpid Security Manager requirement, see _java_toolchain_fact()) that has
        # nothing to do with what jdtls itself needs to run. Launching without
        # inheriting JAVA_HOME lets jdtls's own wrapper script fall back to its
        # sensible built-in default, confirmed live to work.
        jdtls_env = {k: v for k, v in os.environ.items() if k != "JAVA_HOME"}
        self.process = await asyncio.create_subprocess_exec(
            self.jdtls_path, "-data", self._data_dir,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=jdtls_env,
        )
        self._reader_task = asyncio.create_task(self._read_loop())
        root_uri = "file://" + self._mirror
        await self._request(
            "initialize",
            {"processId": os.getpid(), "rootUri": root_uri, "capabilities": {}},
            timeout=JDTLS_INIT_TIMEOUT_SECONDS,
        )
        self._notify("initialized", {})

    async def check_file(
        self, file_path: str, content: str, timeout: float = JDTLS_DIAGNOSTICS_TIMEOUT_SECONDS
    ) -> List[Dict[str, Any]]:
        """Opens (or updates, if already open) the given file with `content`
        and waits for jdtls to push back textDocument/publishDiagnostics for
        it. Diagnostics arrive asynchronously, not as a direct RPC response -
        polls briefly rather than blocking on a request/response pair that
        doesn't exist in the protocol for this. Returns whatever arrived
        within `timeout` (possibly empty, never raises on timeout - a slow or
        stuck check degrades to "no LSP grounding for this attempt", not a
        failure)."""
        self._sync_mirror()
        uri = "file://" + self._mirror_path(file_path)
        self._diagnostics.pop(uri, None)
        if uri in self._open_docs:
            self._open_docs[uri] += 1
            self._notify("textDocument/didChange", {
                "textDocument": {"uri": uri, "version": self._open_docs[uri]},
                "contentChanges": [{"text": content}],
            })
        else:
            self._open_docs[uri] = 1
            self._notify("textDocument/didOpen", {
                "textDocument": {"uri": uri, "languageId": "java", "version": 1, "text": content}
            })

        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if uri in self._diagnostics:
                return self._diagnostics[uri]
            await asyncio.sleep(0.2)
        return self._diagnostics.get(uri, [])

    def _mirror_path(self, file_path: str) -> str:
        """The mirror path (canonical, so it matches the URIs JDTLS
        publishes) of a candidate file."""
        rel = os.path.relpath(os.path.realpath(file_path), os.path.realpath(self.project_root))
        if rel == os.pardir or rel.startswith(os.pardir + os.sep) or os.path.isabs(rel):
            raise ValueError(f"{file_path!r} is outside the analyzed project {self.project_root!r}")
        return os.path.join(self._mirror, rel)

    def _sync_mirror(self) -> None:
        """Make the mirror hold the candidate's current source files: copy
        new/changed files (stat-compared), drop deleted ones. Kriya's state,
        VCS data and build output are never mirrored. Reads the candidate
        only."""
        root = os.path.realpath(self.project_root)
        seen = set()
        for directory, subdirs, files in os.walk(root):
            subdirs[:] = [d for d in subdirs if d not in _MIRROR_SKIP_DIRS]
            for name in files:
                if name in _MIRROR_SKIP_FILES:
                    continue
                source = os.path.join(directory, name)
                rel = os.path.relpath(source, root)
                try:
                    st = os.stat(source)
                except FileNotFoundError:
                    continue
                stamp = (st.st_size, st.st_mtime_ns)
                seen.add(rel)
                if self._mirrored.get(rel) == stamp:
                    continue
                target = os.path.join(self._mirror, rel)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.copyfile(source, target)
                self._mirrored[rel] = stamp
        for rel in set(self._mirrored) - seen:
            try:
                os.remove(os.path.join(self._mirror, rel))
            except FileNotFoundError:
                pass
            del self._mirrored[rel]

    async def shutdown(self) -> None:
        try:
            await asyncio.wait_for(self._request("shutdown", {}, timeout=10), timeout=10)
            self._notify("exit", {})
        except Exception as ex:
            logger.debug(f"jdtls shutdown handshake failed, terminating directly: {ex}")
        finally:
            await self._release()

    async def _release(self) -> None:
        """Cancel the reader, stop the process (SIGTERM, then SIGKILL) and
        remove the data dir - shared by shutdown() and a failed start()."""
        if self._reader_task:
            self._reader_task.cancel()
        try:
            if self.process:
                # SIGTERM alone, with no wait/confirmation and no SIGKILL
                # fallback, is the same leaked-orphan-subprocess bug class
                # already fixed for validate.py's _run_cmd_with_timeout() -
                # jdtls not honoring SIGTERM promptly would otherwise leave
                # an orphan process (and its temp data dir) behind across
                # separate generation runs.
                try:
                    self.process.terminate()
                    await asyncio.wait_for(self.process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    try:
                        self.process.kill()
                        await self.process.wait()
                    except ProcessLookupError:
                        pass
                except ProcessLookupError:
                    pass
                except Exception as ex:
                    logger.debug(f"jdtls process termination failed: {ex}")
        finally:
            for attr in ("_data_dir", "_mirror"):
                path = getattr(self, attr)
                if path:
                    shutil.rmtree(path, ignore_errors=True)
                    setattr(self, attr, None)

    async def _read_loop(self) -> None:
        while True:
            try:
                message = await self._read_message()
            except (ValueError, json.JSONDecodeError) as ex:
                # A malformed Content-Length header or invalid JSON body means
                # message framing is lost - there's no safe way to resync a
                # raw stdio LSP stream, so this genuinely ends grounding for
                # the rest of the run. WARNING (not DEBUG), matching the
                # project's own established pattern for an optional feature's
                # failure path (toolchain/skill warnings) - a DEBUG-only line
                # would let this end silently with no operator visibility.
                logger.warning(
                    f"jdtls sent a malformed message (bad Content-Length header or invalid JSON) - "
                    f"LSP grounding is now disabled for the rest of this run: {ex}"
                )
                break
            except Exception as ex:
                logger.debug(f"jdtls read loop stopped: {ex}")
                break
            if message is None:
                break
            if "id" in message and ("result" in message or "error" in message):
                fut = self._pending.pop(message["id"], None)
                if fut and not fut.done():
                    if "error" in message:
                        fut.set_exception(RuntimeError(str(message["error"])))
                    else:
                        fut.set_result(message.get("result"))
            elif message.get("method") == "textDocument/publishDiagnostics":
                params = message.get("params", {})
                uri = params.get("uri", "")
                if uri:
                    self._diagnostics[uri] = params.get("diagnostics", [])

    async def _read_message(self) -> Optional[Dict[str, Any]]:
        if not self.process or not self.process.stdout:
            return None
        headers: Dict[str, str] = {}
        while True:
            line = await self.process.stdout.readline()
            if not line:
                return None
            decoded = line.decode("utf-8", errors="replace").rstrip("\r\n")
            if decoded == "":
                break
            if ":" in decoded:
                key, _, value = decoded.partition(":")
                headers[key.strip().lower()] = value.strip()
        length = int(headers.get("content-length", "0") or "0")
        if length <= 0:
            return None
        body = await self.process.stdout.readexactly(length)
        return json.loads(body.decode("utf-8"))

    def _write_message(self, message: Dict[str, Any]) -> None:
        if not self.process or not self.process.stdin:
            return
        body = json.dumps(message).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("utf-8")
        self.process.stdin.write(header + body)

    async def _request(self, method: str, params: Dict[str, Any], timeout: float) -> Any:
        msg_id = self._next_id
        self._next_id += 1
        fut: "asyncio.Future[Any]" = asyncio.get_event_loop().create_future()
        self._pending[msg_id] = fut
        self._write_message({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params})
        if self.process and self.process.stdin:
            await self.process.stdin.drain()
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            # A timed-out request's future was never popped by _read_loop
            # (its response, if it ever arrives, is now unwanted) - without
            # this, self._pending grows for the rest of the client's
            # lifetime on every timeout.
            self._pending.pop(msg_id, None)

    def _notify(self, method: str, params: Dict[str, Any]) -> None:
        self._write_message({"jsonrpc": "2.0", "method": method, "params": params})


def format_diagnostics_for_prompt(filepath: str, diagnostics: List[Dict[str, Any]]) -> str:
    """Formats jdtls diagnostics (errors only) into a deliberately forceful,
    unambiguous prompt block - this is the project's own, already-resolved
    ground truth confirming a specific mistake, not a suggestion the model is
    free to weigh against its own assumption. Empty string if there are no
    error-severity diagnostics."""
    errors = [d for d in diagnostics if d.get("severity") == LSP_SEVERITY_ERROR]
    if not errors:
        return ""
    lines = []
    for d in errors:
        line_no = d.get("range", {}).get("start", {}).get("line", 0) + 1
        message = d.get("message", "").strip()
        lines.append(f"  [line {line_no}] {message}")
    diagnostics_text = "\n".join(lines)
    return (
        f"\n=== LANGUAGE SERVER GROUND TRUTH for '{filepath}' - READ THIS CAREFULLY ===\n"
        "This is not a guess, and it is not something to weigh against your own assumption. "
        "A real language server just checked this file against the ACTUAL resolved project "
        "classpath on disk - the real dependencies, the real package structure - and confirms "
        "the following is definitely, unambiguously wrong:\n"
        f"{diagnostics_text}\n"
        "If you regenerate this file without genuinely fixing every one of these, the exact "
        "same error WILL happen again - there is no version of this code that compiles while "
        "this remains true. Do not rationalize around it or assume you already know better. "
        "Fix precisely what is stated above.\n"
    )
