"""``kriya metrics`` (PRD-033): the metrics report and the trusted adjudication of past runs (full modularization
E14; TARGET-MODULE-MAP §3.4).

Moved verbatim from ``kriya/cli.py`` (no test patches a name these bodies read; every function-local import is kept
where it was). ``kriya/cli.py`` registers the group on ``main`` and re-exports every name moved here. This module
never imports ``kriya.cli`` or ``kriya.repl``.
"""
import json
import os
import sys
from typing import Optional, Tuple

import click

from kriya.cli_commands.common import _user_error_text
from kriya.config import AppConfig


@click.group(name="metrics")
def metrics_group() -> None:
    """PRD-033: production metrics derived from persisted run evidence, and
    the trusted adjudication of past runs (false success, regression escape).
    Adjudications are recorded ONLY here; never from a model's opinion."""


@metrics_group.command(name="report")
@click.option("--since", help="Only trace rows at or after this time (YYYY-MM-DD[ HH:MM:SS]).")
@click.option("--workspace", "workspaces", multiple=True, type=click.Path(file_okay=False),
              help="Also derive RunRecord metrics (resume invalidation, recovery) from this workspace; repeatable.")
@click.option("--chaos-report", "chaos_report", type=click.Path(exists=True, dir_okay=False),
              help="A PRD-032 chaos-report.json to include.")
@click.option("--thresholds", type=click.Path(exists=True, dir_okay=False),
              help="An operator thresholds file (outside the workspace); none ship with Kriya.")
@click.option("--out", "out_dir", type=click.Path(file_okay=False),
              help="Write metrics-report.json and metrics-report.md here.")
@click.option("--json", "json_output", is_flag=True, help="Print the report as JSON.")
@click.pass_context
def metrics_report(
    ctx: click.Context, since: Optional[str], workspaces: Tuple[str, ...], chaos_report: Optional[str],
    thresholds: Optional[str], out_dir: Optional[str], json_output: bool,
) -> None:
    """Derive the metrics report (exit 1 when a configured threshold FAILs)."""
    from kriya.core.state_paths import historical_default_trace_db, trace_db_path
    from kriya.metrics.adjudication import load_adjudications
    from kriya.metrics.evidence import load_chaos_report, load_run_record_facts, load_trace_runs
    from kriya.metrics.report import build_report, render_markdown, write_report
    from kriya.metrics.thresholds import load_thresholds

    cfg: AppConfig = ctx.obj["config"]
    trace_db = trace_db_path(cfg)
    try:
        loaded = load_thresholds(thresholds, workspace_root=os.path.realpath(os.getcwd())) if thresholds else None
        report = build_report(
            load_trace_runs(trace_db, since=since), adjudications=load_adjudications(),
            run_records=load_run_record_facts([os.path.realpath(w) for w in workspaces]) if workspaces else None,
            chaos=load_chaos_report(chaos_report) if chaos_report else None,
            thresholds=loaded[0] if loaded else None, thresholds_digest=loaded[1] if loaded else None,
            generated={
                "trace_db": trace_db,
                # LEGACY-TRACES-MIGRATION-001: a pre-move database is named, never read.
                "legacy_trace_db_present": os.path.exists(historical_default_trace_db()),
            },
        )
    except ValueError as error:
        click.secho(f"metrics report refused: {_user_error_text(error)}", fg="red", err=True)
        sys.exit(2)
    if out_dir:
        json_path, md_path = write_report(out_dir, report)
        click.echo(f"Wrote {json_path} and {md_path}", err=json_output)
    click.echo(json.dumps(report, indent=2, sort_keys=True) if json_output else render_markdown(report))
    if report["content"]["thresholds"]["status"] == "FAIL":
        sys.exit(1)


@metrics_group.command(name="adjudicate")
@click.argument("run_id")
@click.option("--verdict", required=True, type=click.Choice(["false_success", "regression_escape", "confirmed_success"]))
@click.option("--evidence", required=True, help="What proves the verdict (a bug, a failing test, a review).")
@click.option("--adjudicator", required=True, help="Who adjudicates.")
@click.option("-y", "--yes", is_flag=True, help="Record without asking.")
@click.pass_context
def metrics_adjudicate(ctx: click.Context, run_id: str, verdict: str, evidence: str, adjudicator: str,
                       yes: bool) -> None:
    """Record a human verdict on a traced run (the only way one is recorded)."""
    from kriya.config.authority_approval import validate_trust_path_outside_workspace
    from kriya.core.state_paths import trace_db_path
    from kriya.metrics.adjudication import AdjudicationStoreError, record_adjudication, store_path
    from kriya.metrics.evidence import load_trace_runs

    cfg: AppConfig = ctx.obj["config"]
    path = store_path()
    try:
        validate_trust_path_outside_workspace(path, os.path.realpath(os.getcwd()))
    except ValueError as error:
        click.secho(f"adjudication refused: {_user_error_text(error)}", fg="red", err=True)
        sys.exit(2)
    status = next((run.status for run in load_trace_runs(trace_db_path(cfg)) if run.run_id == run_id), None)
    click.echo(f"Run {run_id}: {status or 'not traced'}; verdict {verdict}; adjudicator {adjudicator}")
    if not yes and not click.confirm("Record this adjudication?", default=False):
        click.echo("No adjudication recorded.")
        sys.exit(1)
    try:
        record = record_adjudication(run_id=run_id, verdict=verdict, adjudicator=adjudicator, evidence=evidence,
                                     run_status=status, path=path)
    except (AdjudicationStoreError, ValueError, OSError) as error:
        click.secho(f"adjudication not recorded: {error}", fg="red", err=True)
        sys.exit(2)
    click.secho(f"Recorded {record.adjudication_id} ({verdict}) for run {run_id} in {path}", fg="green")


@metrics_group.command(name="adjudications")
@click.option("--json", "json_output", is_flag=True)
def metrics_adjudications(json_output: bool) -> None:
    """List recorded adjudications (read-only)."""
    from kriya.metrics.adjudication import adjudications_for_listing, load_adjudications

    store = load_adjudications()
    payload = {"store": store.path, "status": store.status, "error": store.error,
               "adjudications": adjudications_for_listing(store)}
    if json_output:
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
    else:
        click.echo(f"Store: {store.path} ({store.status}{': ' + store.error if store.error else ''})")
        for item in payload["adjudications"]:
            click.echo(f"  {item['adjudication_id']}  {item['run_id']}  {item['verdict']}  "
                       f"{item['source']}  {item['adjudicator']}  {item['recorded_at']}")
    if store.status == "invalid":
        sys.exit(2)
