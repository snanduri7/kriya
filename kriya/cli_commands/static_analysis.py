"""``kriya static-analysis`` (PRD-031A): the gate's status, an operator scan, and trusted risk acceptance (full
modularization E14; TARGET-MODULE-MAP §3.4).

Moved verbatim from ``kriya/cli.py`` (no test patches a name these bodies read; every function-local import is kept
where it was). ``kriya/cli.py`` registers the group on ``main`` and re-exports every name moved here. This module
never imports ``kriya.cli`` or ``kriya.repl``.
"""
import json
import os
import sys
from typing import Optional, Tuple

import click

from kriya.config import AppConfig


@click.group(name="static-analysis")
def static_analysis_group() -> None:
    """PRD-031A: the static-analysis gate's status, an operator scan, and
    trusted risk acceptance. Waivers are created and revoked ONLY here;
    no model, repository or candidate can create one."""


def _static_analysis_workspace() -> str:
    return os.path.realpath(os.getcwd())


@static_analysis_group.command(name="status")
@click.option("--json", "json_output", is_flag=True, help="Emit the status as JSON.")
@click.pass_context
def static_analysis_status(ctx: click.Context, json_output: bool) -> None:
    """Configuration, provider identity/capability and waiver store (read-only)."""
    from kriya.static_analysis.doctor import static_analysis_status_report

    report = static_analysis_status_report(ctx.obj["config"], _static_analysis_workspace())
    if json_output:
        click.echo(json.dumps(report, indent=2, sort_keys=True, default=str))
        return
    for key, value in report.items():
        click.echo(f"{key}: {json.dumps(value, sort_keys=True, default=str)}")


@static_analysis_group.command(name="scan")
@click.option("--base", default="HEAD", show_default=True, help="Git revision the working tree is compared with.")
@click.option("--json", "json_output", is_flag=True, help="Emit the full evidence as JSON.")
@click.pass_context
def static_analysis_scan(ctx: click.Context, base: str, json_output: bool) -> None:
    """Evaluate the gate on the working tree's changes against BASE. Read-only:
    nothing is committed. Exit 0 only when the result would permit a commit."""
    from kriya.static_analysis.operator_scan import OperatorScanError, run_operator_scan
    from kriya.static_analysis.service import banner

    try:
        result = run_operator_scan(ctx.obj["config"], _static_analysis_workspace(), base)
    except OperatorScanError as error:
        click.secho(f"static-analysis scan failed: {error}", fg="red")
        sys.exit(2)
    if json_output:
        click.echo(json.dumps(dict(result.evidence), indent=2, sort_keys=True, default=str))
    else:
        click.secho(banner(result), fg="green" if result.outcome.value == "PASS" else "yellow", bold=True)
        click.echo(f"permits_commit: {result.permits_commit}")
        if result.gap:
            click.echo(result.gap)
        click.echo(f"evidence: {result.evidence.get('evidence_path')}")
    sys.exit(0 if result.permits_commit else 1)


@static_analysis_group.command(name="waive")
@click.option("--id", "waiver_id", required=True, help="Stable waiver id, e.g. SAW-2026-0001.")
@click.option("--provider", required=True, help="Provider name (as configured).")
@click.option("--rule", "rule_id", required=True, help="Exact normalized rule id (<provider>:<rule>).")
@click.option("--path", "paths", required=True, multiple=True, help="Path or glob scope (repeatable).")
@click.option("--reason", required=True)
@click.option("--owner", required=True, help="The accountable owner/authority.")
@click.option("--max-severity", default="high", show_default=True,
              type=click.Choice(["critical", "high", "medium", "low", "info"]))
@click.option("--classification", "classifications", multiple=True,
              type=click.Choice(["existing", "introduced", "worsened"]),
              help="Finding classes covered (default: existing only).")
@click.option("--fingerprint", default=None, help="Restrict to one exact finding fingerprint.")
@click.option("--rule-pack-digest", default=None, help="Apply only under this exact rule-pack digest.")
@click.option("--tracking-ref", default=None)
@click.option("--expires", "expires_at", default=None, help="ISO-8601 expiry with timezone, e.g. 2026-12-31T00:00:00Z.")
@click.option("-y", "--yes", is_flag=True, help="Skip the confirmation prompt.")
@click.pass_context
def static_analysis_waive(
    ctx: click.Context, waiver_id: str, provider: str, rule_id: str, paths: Tuple[str, ...], reason: str,
    owner: str, max_severity: str, classifications: Tuple[str, ...], fingerprint: Optional[str],
    rule_pack_digest: Optional[str], tracking_ref: Optional[str], expires_at: Optional[str], yes: bool,
) -> None:
    """Record an operator risk acceptance (disposition accepted_risk)."""
    from kriya.static_analysis.waivers import WaiverStoreError, new_waiver, waiver_store_path, write_waiver

    cfg: AppConfig = ctx.obj["config"]
    workspace = _static_analysis_workspace()
    try:
        path = waiver_store_path(cfg.static_analysis.waivers.store, workspace)
        record = new_waiver(
            workspace_root=workspace, waiver_id=waiver_id, provider=provider, rule_id=rule_id, paths=paths,
            reason=reason, owner=owner, max_severity=max_severity,
            classifications=classifications or ("existing",), fingerprint=fingerprint,
            rule_pack_digest=rule_pack_digest, tracking_ref=tracking_ref, expires_at=expires_at,
        )
    except ValueError as error:
        click.secho(f"waiver refused: {error}", fg="red")
        sys.exit(2)
    click.echo(json.dumps(record.to_dict(), indent=2, sort_keys=True))
    if expires_at is None:
        click.secho("WARNING: this waiver never expires; `kriya doctor --production` will warn about it.", fg="yellow")
    if not yes and not click.confirm("Record this ACCEPTED RISK waiver?", default=False):
        click.echo("No waiver recorded.")
        sys.exit(1)
    try:
        write_waiver(path, workspace, record)
    except (WaiverStoreError, OSError) as error:
        click.secho(f"waiver not recorded: {error}", fg="red")
        sys.exit(2)
    click.secho(f"Recorded waiver {waiver_id} in {path}", fg="green")


@static_analysis_group.command(name="revoke")
@click.argument("waiver_id")
@click.pass_context
def static_analysis_revoke(ctx: click.Context, waiver_id: str) -> None:
    """Revoke a waiver; takes effect on the next gate evaluation."""
    from kriya.static_analysis.waivers import revoke_waiver, waiver_store_path

    cfg: AppConfig = ctx.obj["config"]
    workspace = _static_analysis_workspace()
    try:
        removed = revoke_waiver(waiver_store_path(cfg.static_analysis.waivers.store, workspace), workspace, waiver_id)
    except (ValueError, OSError) as error:
        click.secho(f"revoke failed: {error}", fg="red")
        sys.exit(2)
    if not removed:
        click.secho(f"No waiver {waiver_id!r}.", fg="yellow")
        sys.exit(1)
    click.secho(f"Revoked waiver {waiver_id}.", fg="green")


@static_analysis_group.command(name="waivers")
@click.option("--expired", is_flag=True, help="Only expired waivers.")
@click.option("--json", "json_output", is_flag=True)
@click.pass_context
def static_analysis_waivers(ctx: click.Context, expired: bool, json_output: bool) -> None:
    """List this workspace's waivers (read-only)."""
    from kriya.static_analysis.waivers import is_expired, load_waivers, waiver_store_path

    cfg: AppConfig = ctx.obj["config"]
    workspace = _static_analysis_workspace()
    try:
        store = load_waivers(waiver_store_path(cfg.static_analysis.waivers.store, workspace), workspace)
    except ValueError as error:
        click.secho(f"waiver store unusable: {error}", fg="red")
        sys.exit(2)
    records = [r for r in store.records if not expired or is_expired(r)]
    if json_output:
        click.echo(json.dumps({"store": store.path, "status": store.status, "error": store.error,
                               "waivers": [r.to_dict() for r in records]}, indent=2, sort_keys=True))
        return
    click.echo(f"store: {store.path} ({store.status}{': ' + store.error if store.error else ''})")
    for record in records:
        state = "EXPIRED" if is_expired(record) else "active"
        click.echo(f"  {record.waiver_id} [{state}] {record.rule_id} {', '.join(record.paths)} "
                   f"owner={record.owner} expires={record.expires_at or 'never'}")
