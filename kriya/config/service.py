"""``ConfigService`` (full modularization D3; MODULE-CONTRACTS §3): a request's configuration, resolved ONCE per call from its
``RequestContext``, with its SEC-009 authority outcome.

``resolve(ctx)`` is exactly what a run started in ``ctx.workspace.root`` would load, and exactly what the KUP adapter's ``_load_in``
computed before D3 (moved here unchanged): the folder is entered under CONTEXT's ONE working-directory lock
(kriya/context/working_directory.py explains why it is entered and not passed as a root), ``resolve_config_state()`` classifies it,
``load_config()`` applies the SEC-009 approval check and constructs ``AppConfig``; a malformed file is a typed ``CONFIG_LOAD_FAILED``
blocker, an uncovered protected field a typed ``CONFIG_AUTHORITY_REFUSED`` blocker with the field list - never a traceback. Nothing is
cached: every call resolves again, so an approval granted or revoked a moment ago is seen (SECURITY_AUTHORITY is never read once and
kept). ``ctx.workspace = None`` resolves in place (the process working directory), which is what the CLI's own ``load_config()`` does.

This service decides nothing: what needs approval and what covers it are ``kriya.config.authority``/``authority_approval``; where the
environment points is ``kriya.config.environment`` (injected: a fake ``Environment`` redirects the trust file, and ``log_directory``).
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, Mapping, Optional, Tuple

from kriya.config.environment import Environment, process_environment
from kriya.context.model import RequestContext, ScopeRef
from kriya.context.working_directory import WORKING_DIRECTORY_LOCK, entered

CONFIG_LOAD_FAILED = "CONFIG_LOAD_FAILED"
CONFIG_AUTHORITY_REFUSED = "CONFIG_AUTHORITY_REFUSED"
NOT_SUPPORTED_SCOPE = "NOT_SUPPORTED_SCOPE"
BLOCKER_CODES = frozenset({CONFIG_LOAD_FAILED, CONFIG_AUTHORITY_REFUSED})
# Configuration layers resolved today: the packaged default (system), the folder's kriya.yaml (project) and the operator's own
# environment and approvals (user). A scope of any other kind is refused, never ignored.
SUPPORTED_SCOPE_KINDS = frozenset({"system", "user", "project"})
# MODULE-CONTRACTS §3 members of ResolvedConfiguration that are not here yet, and the slice that brings each (closed).
ARRIVES_LATER: Mapping[str, str] = {
    # the configuration fingerprint is STATE's (control.checkpoint.compute_config_fingerprint): CONFIG may not import STATE
    "fingerprint": "O1",
    # the state directory: its precedence lives in core.state_paths (STATE); the WorkspaceContext-keyed state signatures arrive with it
    "state_dir": "O1",
}


class ConfigScopeNotSupported(ValueError):
    """A requested configuration scope this resolution does not layer (typed: ``reason_code`` NOT_SUPPORTED_SCOPE)."""

    reason_code = NOT_SUPPORTED_SCOPE


@dataclass(frozen=True)
class ConfigAuthorityReport:
    """The SEC-009 outcome of one resolution: ``no_findings``, ``approved`` (protected fields, all covered), ``refused`` (uncovered:
    field, source, classification, reason) or ``failed`` (the configuration could not be read)."""

    status: str
    fields: Tuple[Any, ...] = ()

    def as_dict(self) -> Dict[str, Any]:
        """The form the preflight and the KUP catalog/context operations report (unchanged since GUI M6)."""
        return {"status": self.status, "fields": list(self.fields)}


@dataclass(frozen=True)
class ResolvedConfiguration:
    """One request's configuration: ``app`` (None when it could not be loaded), the resolution ``state`` (None when unreadable),
    the authority report and the typed blocker (None when loaded)."""

    app: Any
    state: Any
    authority: ConfigAuthorityReport
    failure: Optional[Dict[str, str]]
    environment: Environment = field(default_factory=process_environment, compare=False, repr=False)

    @property
    def config_path(self) -> Optional[str]:
        return self.state.config_path if self.state is not None else None

    @property
    def config_source(self) -> Optional[str]:
        return self.state.config_source if self.state is not None else None

    def log_directory(self) -> Tuple[str, str]:
        """(directory, source) of the logs of this configuration (KRIYA_LOG_DIR > logging.directory > ~/.kriya/logs); resolved on
        demand, so an unusable value raises where it is used, as before."""
        from kriya.core.logging_setup import resolve_log_directory

        return resolve_log_directory(self.app, self.environment)

    def as_load_in(self) -> Tuple[Any, Any, Dict[str, Any], Optional[Dict[str, str]]]:
        """(state, cfg, authority, failure): the tuple the KUP ``_load_in`` returned before D3."""
        return self.state, self.app, self.authority.as_dict(), self.failure


def _blocker(code: str, message: str) -> Dict[str, str]:
    return {"code": code, "message": message}


class ConfigService:
    """MODULE-CONTRACTS §3 ``ConfigService``."""

    def __init__(self, environment: Optional[Environment] = None) -> None:
        self._environment = environment if environment is not None else process_environment()

    @property
    def environment(self) -> Environment:
        return self._environment

    @contextmanager
    def inside(self, ctx: RequestContext) -> Iterator[None]:
        """Hold the working-directory lock and stand in the request's workspace (when it has one) for the whole body: a caller that
        resolves, compares and writes (the configuration approval) does all three under ONE hold."""
        with WORKING_DIRECTORY_LOCK:
            if ctx.workspace is None:
                yield
            else:
                with entered(ctx.workspace.root):
                    yield

    def resolve(self, ctx: RequestContext, *, config_path: Optional[str] = None, trust_file: Optional[str] = None,
                scopes: Tuple[ScopeRef, ...] = ()) -> ResolvedConfiguration:
        """The configuration of ``ctx``'s workspace, resolved now (see the module docstring)."""
        self._refuse_unsupported(scopes)
        with self.inside(ctx):
            return self.resolve_here(config_path=config_path, trust_file=trust_file)

    def resolve_here(self, *, config_path: Optional[str] = None, trust_file: Optional[str] = None) -> ResolvedConfiguration:
        """``resolve`` for a caller already ``inside`` the request's workspace."""
        from kriya.config.authority import ConfigAuthorityError
        from kriya.config.config import load_config, resolve_config_state

        try:
            state = resolve_config_state(config_path)
        except Exception as error:  # noqa: BLE001 - a malformed kriya.yaml fails here, before any classification: a typed blocker, never a traceback
            return self._resolved(None, None, ConfigAuthorityReport("failed"), _blocker(CONFIG_LOAD_FAILED, f"{type(error).__name__}: {str(error)[:300]}"))
        try:
            cfg = load_config(config_path, trust_file=trust_file, environment=self._environment)
        except ConfigAuthorityError as error:
            fields = tuple({"field": v.field_path, "source": v.source.value, "classification": v.classification.value, "reason": v.reason}
                           for v in sorted(error.violations, key=lambda x: x.field_path))
            return self._resolved(state, None, ConfigAuthorityReport("refused", fields), _blocker(
                CONFIG_AUTHORITY_REFUSED,
                f"{len(fields)} field(s) in this folder's configuration need an operator approval (kriya authority approve): "
                + ", ".join(f["field"] for f in fields[:6])))
        except Exception as error:  # noqa: BLE001 - a malformed or unreadable configuration: typed, never a traceback
            return self._resolved(state, None, ConfigAuthorityReport("failed"), _blocker(CONFIG_LOAD_FAILED, f"{type(error).__name__}: {str(error)[:300]}"))
        status = "approved" if state.violations else "no_findings"
        return self._resolved(state, cfg, ConfigAuthorityReport(status, tuple(v.field_path for v in state.violations)), None)

    def resolution_state(self, *, config_path: Optional[str] = None):
        """Only the classification (``resolve_config_state``), for a caller already ``inside``: what the approval reviews and writes.
        Raises what resolution raises (the caller types it)."""
        from kriya.config.config import resolve_config_state

        return resolve_config_state(config_path)

    def trust_file(self) -> Optional[str]:
        """The operator's trust file in effect (KRIYA_TRUST_FILE of this environment), or None."""
        from kriya.config.environment import trust_file

        return trust_file(self._environment) or None

    def classify(self, field_path: Tuple[Optional[str], str]) -> Any:
        """The static SEC-009 classification of one field (``kriya.config.authority.classify_field``)."""
        from kriya.config.authority import classify_field

        return classify_field(*field_path)

    def _resolved(self, state, app, authority: ConfigAuthorityReport, failure) -> ResolvedConfiguration:
        return ResolvedConfiguration(app=app, state=state, authority=authority, failure=failure, environment=self._environment)

    @staticmethod
    def _refuse_unsupported(scopes: Tuple[ScopeRef, ...]) -> None:
        unsupported = sorted({scope.kind for scope in scopes if scope.kind not in SUPPORTED_SCOPE_KINDS})
        if unsupported:
            raise ConfigScopeNotSupported(f"configuration scope(s) {unsupported} are not resolved by this Kriya (supported: "
                                          f"{sorted(SUPPORTED_SCOPE_KINDS)})")


__all__ = ["ARRIVES_LATER", "BLOCKER_CODES", "CONFIG_AUTHORITY_REFUSED", "CONFIG_LOAD_FAILED", "ConfigAuthorityReport", "ConfigScopeNotSupported",
           "ConfigService", "NOT_SUPPORTED_SCOPE", "ResolvedConfiguration", "SUPPORTED_SCOPE_KINDS"]
