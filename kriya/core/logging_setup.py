"""The one place Kriya decides where its log files live.

Log locations derive from Kriya-controlled state only, never from the
process CWD: an invocation directory must never gain a ``logs/`` directory
merely because Kriya ran there.

- Log directory, first match wins: ``KRIYA_LOG_DIR`` (operator environment),
  then ``logging.directory`` (config, canonicalized once at config load by
  kriya/config/config.py::resolve_config_state and SEC-009-classified), then
  ``~/.kriya/logs`` (the same ``~/.kriya`` home the SEC-009/TOOL-002 approval
  stores use). A relative value is a typed error, never CWD-anchored.
- Application log: ``<log_dir>/kriya.log`` (``logging.file_enabled``).
- Run log: ``<log_dir>/runs/<run_id>/kriya.log`` (``logging.run_file_enabled``),
  attached by kriya/control/run_coordinator.py::begin_mutating_run for the
  lifetime of one mutating run.

There is no per-file path setting; the retired one is rejected at load with
a typed error (kriya/config/config.py::REMOVED_LOGGING_FILE_MESSAGE).
"""
from __future__ import annotations

import logging
import os
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator, List, Optional, Tuple

ENV_LOG_DIR = "KRIYA_LOG_DIR"
APPLICATION_LOG_FILENAME = "kriya.log"
RUN_LOGS_DIRNAME = "runs"

LOG_DIR_SOURCE_ENV = "env"
LOG_DIR_SOURCE_CONFIG = "config"
LOG_DIR_SOURCE_DEFAULT = "default"

_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


class LogDirectoryError(ValueError):
    """The configured log directory is relative, invalid, or unwritable."""


def default_log_directory() -> str:
    """``~/.kriya/logs``, computed at call time (tests point HOME elsewhere)."""
    return os.path.join(os.path.expanduser("~"), ".kriya", "logs")


def canonical_log_directory(value: str, origin: str) -> str:
    """Expand ``~`` and realpath an absolute directory; a relative value is
    refused rather than anchored to the CWD."""
    if not isinstance(value, str) or not value.strip():
        raise LogDirectoryError(f"{origin} must be a non-empty absolute directory path.")
    expanded = os.path.expanduser(value)
    if not os.path.isabs(expanded):
        raise LogDirectoryError(
            f"{origin} must be an absolute path (got {value!r}); Kriya never resolves "
            "a log directory against the current working directory."
        )
    return os.path.realpath(expanded)


def resolve_log_directory(cfg) -> Tuple[str, str]:
    """(directory, source) with precedence env > config > default. Pure: no
    filesystem side effects."""
    env_value = os.environ.get(ENV_LOG_DIR)
    if env_value:
        return canonical_log_directory(env_value, ENV_LOG_DIR), LOG_DIR_SOURCE_ENV
    configured = getattr(cfg.logging, "directory", None)
    if configured is not None:
        return canonical_log_directory(configured, "logging.directory"), LOG_DIR_SOURCE_CONFIG
    return os.path.realpath(default_log_directory()), LOG_DIR_SOURCE_DEFAULT


def application_log_path(log_dir: str) -> str:
    return os.path.join(log_dir, APPLICATION_LOG_FILENAME)


def run_log_path(log_dir: str, run_id: str) -> str:
    from kriya.control.persistence import _valid_run_id

    if not _valid_run_id(run_id):
        raise ValueError("run_id must contain only letters, digits, '-' or '_'")
    return os.path.join(log_dir, RUN_LOGS_DIRNAME, run_id, APPLICATION_LOG_FILENAME)


@dataclass(frozen=True)
class _RunLogSettings:
    log_dir: str
    level: int
    run_file_enabled: bool


_run_log_settings: Optional[_RunLogSettings] = None


def _open_file_handler(path: str, level: int) -> logging.FileHandler:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        handler = logging.FileHandler(path)
    except OSError as error:
        raise LogDirectoryError(f"Cannot write log file {path!r}: {error}") from error
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(_FORMAT))
    return handler


def configure_logging(cfg, file_logging: bool = True) -> None:
    """Root console handler plus, when enabled, the application log file.

    ``file_logging=False`` keeps it console-only (the non-mutating production
    doctor) and enables no run logs. A no-op when the root logger already has
    handlers (a REPL line re-entering the CLI, or a test harness).
    Raises LogDirectoryError when the log directory is invalid or unwritable."""
    global _run_log_settings
    root = logging.getLogger()
    if root.handlers:
        return

    level = getattr(logging, cfg.logging.level.upper(), logging.INFO)
    handlers: List[logging.Handler] = []
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(logging.Formatter(_FORMAT))
    handlers.append(console)

    settings: Optional[_RunLogSettings] = None
    if file_logging and (cfg.logging.file_enabled or cfg.logging.run_file_enabled):
        log_dir, _source = resolve_log_directory(cfg)
        if cfg.logging.file_enabled:
            handlers.append(_open_file_handler(application_log_path(log_dir), level))
        else:
            try:
                os.makedirs(log_dir, exist_ok=True)
            except OSError as error:
                raise LogDirectoryError(f"Cannot create log directory {log_dir!r}: {error}") from error
            if not os.access(log_dir, os.W_OK | os.X_OK):
                raise LogDirectoryError(f"Log directory {log_dir!r} is not writable.")
        settings = _RunLogSettings(log_dir, level, cfg.logging.run_file_enabled)

    logging.basicConfig(level=level, handlers=handlers)
    _run_log_settings = settings


def reset_logging_state() -> None:
    """Forget the recorded run-log settings (tests only)."""
    global _run_log_settings
    _run_log_settings = None


@contextmanager
def run_log(run_id: str, workspace_path: str) -> Iterator[Optional[str]]:
    """Attach ``<log_dir>/runs/<run_id>/kriya.log`` to the root logger for the
    duration of one run; yields its path, or None when run logging is off or
    logging was never configured. A run log that cannot be opened is reported
    and skipped: the log directory was validated at bootstrap, and a log
    failure must never fail or alter the run itself."""
    settings = _run_log_settings
    if settings is None or not settings.run_file_enabled:
        yield None
        return
    path = run_log_path(settings.log_dir, run_id)
    try:
        handler = _open_file_handler(path, settings.level)
    except LogDirectoryError as error:
        logging.getLogger(__name__).warning("Run %s: run log not attached: %s", run_id, error)
        yield None
        return
    handler.stream.write(f"# Kriya run {run_id} workspace {workspace_path}\n")
    handler.flush()
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        yield path
    finally:
        root.removeHandler(handler)
        handler.close()
