"""Recording harness for the REPOSITORY write/commit/snapshot mechanism (full modularization, slice M1).

``Recorder`` wraps the os-level operations the mechanism performs (``open``, ``os.open``, ``os.fdopen``,
``tempfile.mkstemp``, ``os.fsync``, ``os.close``, ``os.replace``, ``os.rename``, ``os.unlink``, ``os.remove``,
``os.chmod``, ``os.makedirs``, ``os.mkdir``, ``os.rmdir``, ``os.umask``, ``os.listdir``) on the shared module objects
(``builtins``, ``os``, ``tempfile``), so it observes the mechanism wherever it lives - the same harness runs before
and after the move to ``kriya/repository/``. Only paths under the scenario root are recorded; temp names are numbered
in creation order, the root and the process id are replaced by tokens, and ``time.time``/``uuid.uuid4`` are pinned,
so a recording is byte-identical across runs and machines. Faults are injected at the os level only
(``Recorder.fault``), never by patching a mechanism function, so no injection depends on the module layout.
"""
from __future__ import annotations

import builtins
import hashlib
import os
import stat
import tempfile
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

_OS_OPS = ("open", "fdopen", "fsync", "close", "replace", "rename", "unlink", "remove", "chmod", "makedirs",
           "mkdir", "rmdir", "umask", "listdir")
FaultHook = Callable[[str, Tuple[Any, ...]], Optional[BaseException]]


class Recorder:
    """Context manager recording the os-level calls made under ``root``."""

    def __init__(self, root: str, fault: Optional[FaultHook] = None) -> None:
        self.roots = sorted({root, os.path.realpath(root)}, key=len, reverse=True)
        self.fault = fault
        self.raw: List[Tuple[Any, ...]] = []
        self.fds: Dict[int, Tuple[str, Tuple[int, int]]] = {}
        self.temp_names: Dict[str, str] = {}
        self.real: Dict[str, Any] = {}
        self._clock = 1_000_000.0

    # ------------------------------------------------------------ helpers
    def _bind_fd(self, fd: int, path: str) -> None:
        info = os.fstat(fd)
        self.fds[fd] = (path, (info.st_dev, info.st_ino))

    def fd_path(self, fd: Any) -> Optional[str]:
        """The recorded path of ``fd`` while it still refers to the file opened under that path (a descriptor number
        reused after a native close - a pipe, say - is never attributed to the old path)."""
        entry = self.fds.get(fd) if isinstance(fd, int) else None
        if entry is None:
            return None
        try:
            info = os.fstat(fd)
        except OSError:
            self.fds.pop(fd, None)
            return None
        if (info.st_dev, info.st_ino) != entry[1]:
            self.fds.pop(fd, None)
            return None
        return entry[0]

    def inside(self, path: Any) -> bool:
        if isinstance(path, int):
            return self.fd_path(path) is not None
        try:
            text = os.fsdecode(path)
        except TypeError:
            return False
        return any(text == root or text.startswith(root + os.sep) for root in self.roots)

    def norm(self, value: Any) -> Any:
        if isinstance(value, (list, tuple)):
            return [self.norm(item) for item in value]
        if isinstance(value, dict):
            return {self.norm(key): self.norm(item) for key, item in value.items()}
        if not isinstance(value, str):
            return value
        text = value
        for actual, placeholder in sorted(self.temp_names.items(), key=lambda item: -len(item[0])):
            text = text.replace(actual, placeholder)
        for root in self.roots:
            text = text.replace(root, "<root>")
        return text.replace(f"-{os.getpid()}", "-<pid>")

    def calls(self) -> List[List[Any]]:
        return [self.norm(list(entry)) for entry in self.raw]

    def _record(self, *entry: Any) -> None:
        self.raw.append(entry)

    def _maybe_fault(self, op: str, args: Tuple[Any, ...]) -> None:
        if self.fault is None:
            return
        error = self.fault(op, args)
        if error is not None:
            self._record("FAULT", op, *[os.fsdecode(a) if isinstance(a, (str, bytes)) else a for a in args[:2]])
            raise error

    def _path(self, value: Any) -> str:
        return (self.fd_path(value) or "<fd>") if isinstance(value, int) else os.fsdecode(value)

    # ------------------------------------------------------------ wrappers
    def _wrap_open(self, file, mode="r", *args, **kwargs):
        recorded = not isinstance(file, int) and self.inside(file)
        if recorded:
            self._record("open", os.fsdecode(file), mode)
            self._maybe_fault("open", (file, mode))
        handle = self.real["builtins.open"](file, mode, *args, **kwargs)
        if recorded:
            self._bind_fd(handle.fileno(), os.fsdecode(file))
        return handle

    def _wrap_os_open(self, path, flags, *args, **kwargs):
        recorded = self.inside(path)
        if recorded:
            kind = "excl-create" if flags & os.O_EXCL else ("rdonly" if flags == os.O_RDONLY else "other")
            self._record("os.open", os.fsdecode(path), kind)
            self._maybe_fault("os.open", (path, flags))
        fd = self.real["os.open"](path, flags, *args, **kwargs)
        if recorded:
            self._bind_fd(fd, os.fsdecode(path))
        return fd

    def _wrap_mkstemp(self, suffix=None, prefix=None, dir=None, text=False):  # noqa: A002 - tempfile's own signature
        recorded = dir is not None and self.inside(dir)
        if recorded:
            self._record("mkstemp", prefix, suffix, os.fsdecode(dir))
            self._maybe_fault("mkstemp", (prefix, dir))
        fd, path = self.real["tempfile.mkstemp"](suffix=suffix, prefix=prefix, dir=dir, text=text)
        if recorded:
            name = os.path.basename(path)
            self.temp_names[name] = f"{prefix or ''}#{len(self.temp_names) + 1}{suffix or ''}"
            self._bind_fd(fd, path)
        return fd, path

    def _wrap_fd(self, op: str):
        real = self.real[f"os.{op}"]

        def wrapper(fd, *args, **kwargs):
            path = self.fd_path(fd)
            if path is not None:
                self._record(op, path, *[a for a in args if isinstance(a, str)])
                self._maybe_fault(op, (path,))
            result = real(fd, *args, **kwargs)
            if path is not None and op == "close":
                self.fds.pop(fd, None)
            return result
        return wrapper

    def _wrap_paths(self, op: str, arity: int):
        real = self.real[f"os.{op}"]

        def wrapper(*args, **kwargs):
            paths = args[:arity]
            recorded = any(self.inside(p) for p in paths if not isinstance(p, int))
            if recorded:
                extra = []
                if op == "chmod":
                    extra = [oct(args[1])]
                elif op == "makedirs":
                    extra = [f"exist_ok={kwargs.get('exist_ok', args[2] if len(args) > 2 else False)}"]
                self._record(op, *[self._path(p) for p in paths], *extra)
                self._maybe_fault(op, tuple(paths))
            return real(*args, **kwargs)
        return wrapper

    def _wrap_umask(self, mask):
        self._record("umask", oct(mask))
        return self.real["os.umask"](mask)

    def _fake_time(self) -> float:
        self._clock += 1.0
        return self._clock

    def __enter__(self) -> "Recorder":
        self.real = {"builtins.open": builtins.open, "tempfile.mkstemp": tempfile.mkstemp,
                     "time.time": time.time, "uuid.uuid4": uuid.uuid4}
        for op in _OS_OPS:
            self.real[f"os.{op}"] = getattr(os, op)
        self.real["os.open"] = os.open
        builtins.open = self._wrap_open
        os.open = self._wrap_os_open
        tempfile.mkstemp = self._wrap_mkstemp
        for op in ("fdopen", "fsync", "close"):
            setattr(os, op, self._wrap_fd(op))
        for op, arity in (("replace", 2), ("rename", 2), ("unlink", 1), ("remove", 1), ("chmod", 1),
                          ("makedirs", 1), ("mkdir", 1), ("rmdir", 1), ("listdir", 1)):
            setattr(os, op, self._wrap_paths(op, arity))
        os.umask = self._wrap_umask
        time.time = self._fake_time
        uuid.uuid4 = lambda: uuid.UUID("12345678123456781234567812345678")
        return self

    def __exit__(self, *exc: Any) -> None:
        builtins.open = self.real["builtins.open"]
        tempfile.mkstemp = self.real["tempfile.mkstemp"]
        time.time = self.real["time.time"]
        uuid.uuid4 = self.real["uuid.uuid4"]
        for op in _OS_OPS:
            setattr(os, op, self.real[f"os.{op}"])
        os.open = self.real["os.open"]


def tree(root: str, recorder: Recorder) -> List[List[Any]]:
    """Every entry under ``root``: [path, kind, mode, sha256 | link target | None, evidence text | None]."""
    entries = []
    for directory, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name != ".git")   # git's own internals (index mtimes) are not the mechanism
        for name in sorted(dirnames + filenames):
            path = os.path.join(directory, name)
            info = os.lstat(path)
            rel = recorder.norm(os.path.relpath(path, root))
            mode = oct(stat.S_IMODE(info.st_mode))
            if stat.S_ISLNK(info.st_mode):
                entries.append([rel, "link", None, recorder.norm(os.readlink(path)), None])
            elif stat.S_ISDIR(info.st_mode):
                entries.append([rel, "dir", mode, None, None])
            else:
                with recorder.real.get("builtins.open", builtins.open)(path, "rb") as handle:
                    data = handle.read()
                if path.endswith(".json") and "commits" in path:
                    # commit evidence embeds absolute paths in failure messages: digest the normalized text
                    evidence = recorder.norm(data.decode("utf-8"))
                    entries.append([rel, "file", mode, "normalized:" + hashlib.sha256(evidence.encode("utf-8")).hexdigest(), evidence])
                else:
                    entries.append([rel, "file", mode, hashlib.sha256(data).hexdigest(), None])
    return entries


def describe_error(error: BaseException, recorder: Recorder) -> Dict[str, Any]:
    kriya_classes = [cls.__name__ for cls in type(error).__mro__ if cls.__module__.startswith("kriya")]
    cause = error.__cause__
    return {
        "class": type(error).__name__, "kriya_mro": kriya_classes, "message": recorder.norm(str(error)),
        "reason_code": getattr(error, "reason_code", None),
        "cause": None if cause is None else [type(cause).__name__, recorder.norm(str(cause))],
    }
