"""``kriya authority`` (SEC-009 P2): inspect, approve and revoke security-authority configuration (full
modularization E14; TARGET-MODULE-MAP §3.4).

Moved verbatim from ``kriya/cli.py`` (no test patches a name these bodies read; every function-local import is kept
where it was). ``kriya/cli.py`` registers the group on ``main`` (``main()`` still skips ``load_config()`` for
``authority`` by name) and re-exports every name moved here; ``_authority_fail`` is also used by the MCP approval
path that stays in ``kriya/cli.py``. This module never imports ``kriya.cli`` or ``kriya.repl``.
"""
import json
import sys
from typing import NoReturn, Optional

import click

from kriya.cli_commands.common import _user_error_text
from kriya.core.logging_setup import LogDirectoryError


@click.group(name="authority")
def authority_group() -> None:
    """SEC-009 P2: inspect and explicitly, durably approve security-authority
    configuration (mcp.*, plugins.*, execution_policy.*, runtime_profile, and
    similar fields kriya/config/authority.py classifies SECURITY_AUTHORITY/
    PLATFORM_POLICY) that a repository-equivalent source (auto-discovered
    kriya.yaml, an explicit --config) cannot grant itself. Approval is bound
    to the EXACT current effective security configuration (a digest over the
    complete set, not a blanket "trust this repo" bit) and is stored outside
    the workspace, never inside it - see kriya/config/authority_approval.py's
    module docstring for why. Reachable even when the current configuration
    has pending/denied security fields, so this is always the way out of a
    "Configuration-authority denied" error."""
    pass


# The typed errors that mean "the operator must fix the configuration or the
# approval store". Named one by one, never ValueError/Exception: a coding error
# (a failed unpack, a bad int(), a TypeError) must still show its traceback.
def _authority_user_errors() -> tuple:
    import pydantic
    import yaml

    from kriya.config.authority import AutonomyModeError, ConfigAuthorityError, EgressPolicyError
    from kriya.config.authority_approval import ApprovalArtifactError, TrustPathInsideWorkspaceError
    from kriya.config.config import RemovedConfigFieldError
    from kriya.core.state_paths import StateDirectoryError
    return (
        StateDirectoryError, LogDirectoryError, RemovedConfigFieldError, ConfigAuthorityError, AutonomyModeError, EgressPolicyError,
        pydantic.ValidationError, TrustPathInsideWorkspaceError, ApprovalArtifactError,
        OSError, yaml.YAMLError,
    )


def _authority_fail(error: BaseException) -> NoReturn:
    click.secho(f"Error: {_user_error_text(error)}", fg="red", err=True)
    sys.exit(1)


def _authority_local_path(workspace_root: str) -> str:
    """This workspace's local approval store; a store configured inside the
    workspace is refused as a typed operator error, never a traceback."""
    from kriya.config.authority_approval import default_local_approval_path
    try:
        return default_local_approval_path(workspace_root)
    except _authority_user_errors() as error:
        _authority_fail(error)


def _authority_state(ctx: click.Context):
    from kriya.config.config import resolve_config_state
    try:
        return resolve_config_state(ctx.obj.get('config_path'))
    except _authority_user_errors() as error:
        _authority_fail(error)


def _print_pending(pending) -> None:
    if not pending:
        click.echo("No pending security-authority fields - current configuration is fully authorized.")
        return
    click.secho(f"{len(pending)} security-authority field(s) pending approval:", bold=True)
    for p in pending:
        click.echo(f"  - {p.field_path}")
        click.echo(f"      classification: {p.classification}")
        click.echo(f"      source:         {p.source}")
        click.echo(f"      value:          {json.dumps(p.redacted_value, default=str)}")


@authority_group.command(name="inspect")
@click.pass_context
def authority_inspect(ctx: click.Context) -> None:
    """Display the CURRENT effective configuration's pending security-authority
    fields (field path, classification, provenance, and a secret-redacted
    value) and whether an existing local approval currently covers them.
    Read-only - never writes anything, never itself approves."""
    from kriya.config.authority_approval import describe_pending, is_approval_current_for, load_approval_artifact

    state = _authority_state(ctx)
    pending = describe_pending(state.violations, state.config_dict)
    _print_pending(pending)

    if not state.violations:
        return

    path = _authority_local_path(state.workspace_root)
    try:
        artifact = load_approval_artifact(path)
    except Exception as e:
        click.secho(f"\nLocal approval store at {path}: present but invalid ({e}).", fg="yellow")
        return

    if artifact is None:
        click.echo(f"\nNo local approval on file at {path}.")
        click.echo("Run 'kriya authority approve' to grant it.")
        return

    if is_approval_current_for(artifact, state.violations, state.config_dict, state.workspace_root):
        click.secho(f"\nLocal approval at {path} is CURRENT and covers all pending fields.", fg="green")
    else:
        click.secho(
            f"\nLocal approval at {path} exists but does NOT cover the current configuration "
            "(it is stale, tampered, or was granted for a different security-field set) - "
            "the fields above remain denied. Run 'kriya authority approve' again.",
            fg="yellow",
        )


@authority_group.command(name="approve")
@click.option('--out', type=click.Path(), default=None,
              help="Write the approval artifact to this path instead of the default local "
              "per-workspace store (~/.kriya/authority/ by default, override via "
              "KRIYA_AUTHORITY_HOME) - for producing a portable artifact to ship to CI via "
              "your own external channel. Must resolve outside the workspace, same rule as "
              "--trust-file; refused otherwise.")
@click.option('--confirm', is_flag=True, default=False,
              help="Skip the interactive confirmation prompt (for scripted/operator use). "
              "This is a dedicated flag for this command only - it has no relationship to "
              "and is never satisfied by generate/fix's -y flag.")
@click.pass_context
def authority_approve(ctx: click.Context, out: Optional[str], confirm: bool) -> None:
    """Explicitly approve the CURRENT effective security-authority configuration.

    Always re-resolves the configuration fresh (never reuses a prior `inspect`
    call's output - closes the TOCTOU window by construction: what you approve
    is recomputed at the moment of approval, not shown once and trusted
    later). Approval binds to the EXACT set of security-relevant fields and
    their current values - adding, removing, or changing ANY of them
    afterward invalidates this approval entirely; it is not a blanket grant
    to the repository, the config file, or any future field."""
    from kriya.config.authority_approval import (
        describe_pending,
        grant_local_approval,
        validate_trust_path_outside_workspace,
    )

    state = _authority_state(ctx)
    pending = describe_pending(state.violations, state.config_dict)
    _print_pending(pending)

    if not state.violations:
        click.echo("Nothing to approve.")
        return

    out_path = out or _authority_local_path(state.workspace_root)
    if out:
        try:
            validate_trust_path_outside_workspace(out_path, state.workspace_root)
        except _authority_user_errors() as error:
            _authority_fail(error)

    if not confirm:
        if not click.confirm(
            "\nGrant explicit approval to exactly the security-authority field(s) listed above? "
            "Any later change to any of them will require re-approval."
        ):
            click.echo("Not approved - no artifact written.")
            sys.exit(1)

    try:
        artifact, _written = grant_local_approval(state.violations, state.config_dict, state.workspace_root, out_path)
    except _authority_user_errors() as error:
        _authority_fail(error)
    click.secho(
        f"\nApproved. Artifact written to {out_path} (set digest {artifact.set_digest[:16]}...).",
        fg="green", bold=True,
    )
    if not out:
        click.echo(
            "This is the default local store for this workspace - ordinary `kriya generate`/`fix`/etc. "
            "will now honor it automatically. For CI, copy this file via your own external channel and "
            "pass it with --trust-file (or set KRIYA_TRUST_FILE) - Kriya never ships or auto-provisions it."
        )


@authority_group.command(name="revoke")
@click.pass_context
def authority_revoke(ctx: click.Context) -> None:
    """Immediately revoke this workspace's local approval, if any - future
    `load_config()` calls fall back to P1 fail-closed denial for every
    security-authority field, with no need to touch the config itself.
    Idempotent: revoking when nothing is approved is not an error."""
    from kriya.config.authority_approval import revoke_local_approval

    state = _authority_state(ctx)
    path = _authority_local_path(state.workspace_root)
    try:
        removed = revoke_local_approval(state.workspace_root)
    except _authority_user_errors() as error:
        _authority_fail(error)
    if removed:
        click.secho(f"Revoked local approval at {path}.", fg="yellow", bold=True)
    else:
        click.echo(f"No local approval on file at {path} - nothing to revoke.")
