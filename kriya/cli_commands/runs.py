"""``kriya runs`` (PRD-008): inspect, recover and prune a workspace's run records and commit evidence (full
modularization E14; TARGET-MODULE-MAP §3.4).

Moved verbatim from ``kriya/cli.py`` (no test patches a name these bodies read; every function-local import is kept
where it was). ``kriya/cli.py`` registers the group on ``main`` (``main()`` still skips ``load_config()`` for
``runs`` by name) and re-exports every name moved here. This module never imports ``kriya.cli`` or ``kriya.repl``.
"""
import json
import os
import sys
import uuid
from typing import Any, Dict, Optional

import click

from kriya.control.run_ownership import WorkspaceLockHeldError

# PRD-008: `kriya runs` - run lifecycle management. Like `authority`, it is
# reachable even when the current configuration is denied (main() skips
# load_config() for it): recovering a half-committed workspace must never
# depend on loading repository-controlled configuration.
_RUNS_EXIT_ACTION_REQUIRED = 1
_RUNS_EXIT_RUN_ACTIVE = 3


@click.group(name="runs")
def runs_group() -> None:
    """Inspect, recover and prune this workspace's run records and commit
    evidence. Exit codes: 0 = nothing needs attention, 1 = action required
    (see the output), 3 = a live Kriya run holds the workspace lock."""
    pass


def _runs_short(value: Optional[str]) -> str:
    return value[:12] if isinstance(value, str) else "-"


def _runs_state(state: Dict[str, Any]) -> str:
    if not isinstance(state, dict):
        return "?"
    if state.get("outside_workspace"):
        return "outside the workspace"
    if state.get("not_a_regular_file"):
        return "not a regular file"
    if "text_revision" in state:
        return f"text {_runs_short(state['text_revision'])}"
    if not state.get("exists"):
        return "absent"
    mode = state.get("mode")
    return f"{_runs_short(state.get('sha256'))} mode {oct(mode) if isinstance(mode, int) else '?'}"


def _print_recovery_assessment(assessment) -> None:
    from kriya.control import recovery

    click.echo(f"Workspace: {assessment.workspace_path}")
    click.secho(f"Status: {assessment.status}", bold=True)
    if assessment.run_active is not None:
        click.echo(f"A live Kriya run holds the workspace lock ({assessment.run_active}); "
                   "nothing is classified while it runs.")
        return
    for finding in assessment.records:
        click.echo(f"\nRun {finding.run_id}: {finding.lifecycle_state} "
                   f"(commit_result={finding.commit_result})")
        for txid, result in finding.cycle_results.items():
            click.echo(f"  commit {txid}: {result or 'not proven'}")
        for reason in finding.blocked_reasons:
            click.secho(f"  blocked: {reason}", fg="red")
        for txid in finding.completable_transactions:
            click.secho(f"  commit {txid} is partially applied; `kriya runs recover "
                        "--complete-partial` can finish it", fg="yellow")
        if finding.recoverable:
            click.echo(f"  -> RECOVERED, commit_result={finding.proposed_commit_result}, "
                       f"terminal_status={finding.proposed_terminal_status}")
    for finding in assessment.evidence:
        click.echo(f"\nCommit {finding.transaction_id}: {finding.state or 'unreadable'} -> {finding.outcome}"
                   + (f" (run {finding.owner_run_id})" if finding.owner_run_id else ""))
        if finding.error:
            click.secho(f"  unreadable: {finding.error} ({finding.path})", fg="red")
        for item in finding.operations:
            click.echo(f"  {item.classification:<11} {item.kind:<6} {item.target_path}")
            if item.classification != recovery.OP_APPLIED:
                click.echo(f"      before {_runs_state(item.expected_before)} | "
                           f"after {_runs_state(item.expected_after)} | "
                           f"now {_runs_state(item.current)}")
        if finding.outcome == recovery.OUTCOME_PARTIAL:
            if finding.roll_forward_refusal is None:
                click.secho("  --complete-partial can finish this commit", fg="yellow")
            else:
                click.secho(f"  cannot be completed: {finding.roll_forward_refusal}", fg="red")
        if finding.leftover_staged_files:
            click.echo(f"  {len(finding.leftover_staged_files)} staged temp file(s) to remove")
    for path, reason in assessment.unreadable_records:
        click.secho(f"\nUnreadable run record {path}: {reason}", fg="red")
        click.echo("  Kriya never edits or deletes it. Once you have confirmed it is not the only "
                   "evidence of an interrupted commit (no commit above belongs to it and the "
                   "workspace files are as you expect), move it out of .kriya/control/runs/.")
    if assessment.evidence_error:
        click.secho(f"\nCommit evidence could not be listed: {assessment.evidence_error}", fg="red")
    for directory, reason in assessment.nested_workspace_state:
        click.secho(f"\nUnsettled state under {directory} ({reason}): an earlier Kriya treated that sub-directory as its own workspace. It is not in this "
                    "repository's stores, so `kriya runs recover` cannot settle it; settle it with that earlier Kriya or review it by hand before running here.",
                    fg="red")
    if not assessment.nested_scan_complete:
        click.secho("\nThe check for state left under sub-directories by an earlier Kriya did not finish (large tree); it is not proven absent.", fg="yellow")

    advice = {
        recovery.STATUS_CLEAN: "Nothing needs recovery.",
        recovery.STATUS_RECOVERY_AVAILABLE: "Run `kriya runs recover` to settle what the evidence proves.",
        recovery.STATUS_COMPLETE_PARTIAL_REQUIRED: (
            "A commit-eligible candidate was partially applied. `kriya runs recover "
            "--complete-partial` finishes it from its staged files; there is no rollback."
        ),
        recovery.STATUS_MANUAL_ACTION_REQUIRED: (
            "Some state cannot be settled automatically. Restore each listed file to its "
            "`before` or `after` state (or inspect the unreadable files), then run "
            "`kriya runs status` again. `kriya runs recover` still settles everything else."
        ),
    }.get(assessment.status)
    if advice:
        click.echo(f"\n{advice}")


def _runs_workspace_option(function):
    return click.option(
        "--workspace", "workspace", default=".", show_default=True,
        type=click.Path(exists=True, file_okay=False),
        help="Workspace whose runs to inspect.",
    )(function)


@runs_group.command(name="status")
@_runs_workspace_option
@click.option("--json", "as_json", is_flag=True, help="Print the assessment as JSON.")
@click.option("--kup-version", "kup_version", type=int, default=None,
              help="With --json: wrap the assessment in a KUP envelope of this schema version (1). GUI M1 Phase C.")
def runs_status(workspace: str, as_json: bool, kup_version: Optional[int]) -> None:
    """Read-only: classify crashed runs and interrupted commits from their
    durable evidence and the files on disk, and say what `recover` would do.
    Never takes the lock or writes anything; reports RUN_ACTIVE while a live
    run holds the workspace."""
    from kriya.control.recovery import STATUS_CLEAN, STATUS_RUN_ACTIVE, assess_recovery

    if kup_version is not None:
        if not as_json:
            raise click.UsageError("--kup-version requires --json")
        sys.dont_write_bytecode = True
        from kriya.kup.cli_ops import run_kup_operation
        from kriya.kup.inspect import encode_response, error_envelope
        from kriya.kup.policy import KUP_SCHEMA_VERSION, UNSUPPORTED_SCHEMA_VERSION
        if kup_version != KUP_SCHEMA_VERSION:
            click.echo(encode_response(error_envelope("workspace.status", "cli", UNSUPPORTED_SCHEMA_VERSION,
                                                      f"KUP schema version {kup_version} is not supported (supported: {KUP_SCHEMA_VERSION})")))
            return
        env = run_kup_operation(None, {"operation": "workspace.status", "workspace": os.path.abspath(workspace)})
        click.echo(encode_response(env))
        data = env.get("data") or {}
        if data.get("exit_code"):
            sys.exit(int(data["exit_code"]))
        return
    assessment = assess_recovery(workspace)
    if as_json:
        click.echo(json.dumps(assessment.to_dict(), indent=2, sort_keys=True))
    else:
        _print_recovery_assessment(assessment)
    if assessment.status == STATUS_RUN_ACTIVE:
        sys.exit(_RUNS_EXIT_RUN_ACTIVE)
    if assessment.status != STATUS_CLEAN:
        sys.exit(_RUNS_EXIT_ACTION_REQUIRED)


@runs_group.command(name="recover")
@_runs_workspace_option
@click.option("--complete-partial", is_flag=True,
              help="Finish a partially applied commit from its staged candidate files. Only "
                   "allowed when the run's record proves the exact candidate was commit-eligible.")
@click.option("--json", "as_json", is_flag=True, help="Print the report as JSON.")
def runs_recover(workspace: str, complete_partial: bool, as_json: bool) -> None:
    """Settle crashed runs and interrupted commits from what the evidence
    proves (RunRecord -> RECOVERED, never SUCCESS). Takes the workspace lock.
    Source files change only with --complete-partial; there is no rollback."""
    from kriya.control.recovery import STATUS_CLEAN, recover_workspace

    try:
        report = recover_workspace(workspace, complete_partial=complete_partial)
    except WorkspaceLockHeldError as error:
        click.secho(f"RUN_ACTIVE: {error}", fg="red", err=True)
        sys.exit(_RUNS_EXIT_RUN_ACTIVE)
    if as_json:
        click.echo(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        for item in report.settled_evidence:
            click.echo(f"Settled commit {item['transaction_id']}: {item['outcome']} -> {item['state']}")
        for path in report.rolled_forward:
            click.echo(f"Completed: {path}")
        for item in report.recovered_runs:
            click.echo(f"Recovered run {item['run_id']} ({item['prior_lifecycle_state']}): "
                       f"commit_result={item['commit_result']}, terminal_status={item['terminal_status']}")
        if report.removed_staged_files:
            click.echo(f"Removed {len(report.removed_staged_files)} staged temp file(s).")
        for item in report.contract_transitions:
            click.echo(f"Contract registry transition {item['transaction_id']}: {item['outcome']}")
        for error in report.errors:
            click.secho(f"Error: {error}", fg="red")
        click.echo("")
        _print_recovery_assessment(report.after)
    if report.errors or report.after.status != STATUS_CLEAN:
        sys.exit(_RUNS_EXIT_ACTION_REQUIRED)


@runs_group.command(name="prune")
@_runs_workspace_option
@click.option("--keep", type=click.IntRange(min=0), default=None,
              help="Terminal run records to keep (default: the retention default).")
@click.option("--dry-run", is_flag=True, help="Report what would be pruned without deleting.")
@click.option("--json", "as_json", is_flag=True, help="Print the report as JSON.")
def runs_prune(workspace: str, keep: Optional[int], dry_run: bool, as_json: bool) -> None:
    """Reference-safe retention under the workspace lock: never removes a
    non-terminal record, one whose commit state is unknown, one a checkpoint
    or the control state references, or commit evidence a kept record needs;
    nothing at all while any record is unreadable."""
    from kriya.control.recovery import canonical_workspace
    from kriya.control.retention import DEFAULT_KEEP_TERMINAL_RUNS, prune_run_state
    from kriya.control.run_ownership import acquire_run_lock

    canonical = canonical_workspace(workspace)
    try:
        with acquire_run_lock(canonical, run_id=f"prune-{uuid.uuid4().hex[:12]}"):
            report = prune_run_state(
                canonical, dry_run=dry_run,
                keep_terminal_runs=DEFAULT_KEEP_TERMINAL_RUNS if keep is None else keep,
            )
    except WorkspaceLockHeldError as error:
        click.secho(f"RUN_ACTIVE: {error}", fg="red", err=True)
        sys.exit(_RUNS_EXIT_RUN_ACTIVE)
    if as_json:
        click.echo(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        verb = "Would prune" if dry_run else "Pruned"
        click.echo(f"{verb} {len(report.pruned_run_ids)} run record(s) and "
                   f"{len(report.pruned_evidence_ids)} commit evidence file(s); "
                   f"{len(report.protected_run_ids)} run record(s) kept.")
        if report.skipped_reason:
            click.secho(f"Skipped: {report.skipped_reason}", fg="red")
    if report.skipped_reason:
        sys.exit(_RUNS_EXIT_ACTION_REQUIRED)


# Wave 2 Slice A (TOOL EXECUTION DISPOSITION): `kriya runs tool-inspect|tool-abandon|tool-retry` - the operator's disposition of a plan TOOL subtask whose side effect may or may not have happened. A thin
# adapter over kriya/control/tool_disposition.py: it prints that owner's evidence and passes the operator's explicit decision; it never edits state itself, derives lifecycle
# truth, or executes anything. Like the rest of `kriya runs` it needs no configuration (main() skips load_config() for it).
def _print_tool_entry(entry) -> None:
    attempt = "legacy/unversioned (persisted before attempt identity existed)" if entry.legacy_unversioned else str(entry.attempt)
    click.secho(f"Subtask {entry.subtask_id}: {entry.classification}", bold=True)
    click.echo(f"  attempt: {attempt}")
    click.echo(f"  allowed: {', '.join(entry.allowed_actions) or 'none'}")
    for row in entry.history:
        click.echo("  history: " + ", ".join(f"{key}={value}" for key, value in row.items() if key in
                                            ("type", "attempt", "status", "effect_boundary", "action", "operator", "to_attempt", "error")))


@runs_group.command(name="tool-inspect")
@_runs_workspace_option
@click.option("--json", "as_json", is_flag=True, help="Print the report as JSON.")
def runs_tool_inspect(workspace: str, as_json: bool) -> None:
    """Read-only: list the TOOL subtasks awaiting an operator, with their attempt and the kept evidence. Exit codes: 0 = none awaiting, 1 = action required, 3 = a live run holds the workspace."""
    from kriya.context.workspace_identity import WorkspaceOwnershipError
    from kriya.control.tool_disposition import inspect_tool_dispositions

    try:
        report = inspect_tool_dispositions(workspace)
    except WorkspaceOwnershipError as error:
        click.secho(f"Refused: {error}", fg="red", err=True)
        sys.exit(_RUNS_EXIT_ACTION_REQUIRED)
    if as_json:
        click.echo(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        click.echo(f"Workspace: {report.workspace}")
        if report.unreadable_control_state:
            click.secho(f"The control state cannot be read ({report.unreadable_control_state[1]}); nothing can be disposed until it is inspected.", fg="red")
        for entry in report.entries:
            _print_tool_entry(entry)
        if not report.entries and not report.unreadable_control_state:
            click.echo("No TOOL subtask is awaiting an operator.")
    if report.entries or report.unreadable_control_state:
        sys.exit(_RUNS_EXIT_ACTION_REQUIRED)


def _runs_tool_disposition_options(function):
    for option in reversed((
        click.argument("subtask_id"),
        _runs_workspace_option,
        click.option("--attempt", "attempt", type=click.IntRange(min=1), default=None, help="The attempt number shown by `inspect` (required unless --legacy)."),
        click.option("--legacy", "legacy", is_flag=True, help="The subtask is listed as legacy/unversioned (no attempt number)."),
        click.option("--confirm", "confirmed", is_flag=True, help="Explicit confirmation. There is no default: without it nothing changes."),
        click.option("--operator", "operator", default=None, help="Recorded in the audit trail (default: the OS user)."),
        click.option("--json", "as_json", is_flag=True, help="Print the result as JSON."),
    )):
        function = option(function)
    return function


def _runs_tool_dispose(action: str, subtask_id: str, workspace: str, attempt: Optional[int], legacy: bool, confirmed: bool, operator: Optional[str], as_json: bool) -> None:
    import getpass

    from kriya.context.workspace_identity import WorkspaceOwnershipError
    from kriya.control.tool_disposition import ToolDispositionEntry, apply_disposition, inspect_tool_dispositions

    if (attempt is None) == (not legacy):
        raise click.UsageError("give exactly one of --attempt N (as shown by `inspect`) or --legacy.")
    try:
        entry: Optional[ToolDispositionEntry] = next((e for e in inspect_tool_dispositions(workspace).entries if e.subtask_id == subtask_id), None)
        if entry is not None and not as_json:
            _print_tool_entry(entry)
            if entry.state in ("tool_started", "tool_failed"):
                click.secho("Its side effect MAY ALREADY HAVE HAPPENED"
                            + ("; a retry can DUPLICATE it." if action == "retry" else "; abandoning does not undo it."), fg="yellow")
        result = apply_disposition(workspace, action, subtask_id, expected_attempt=attempt, operator=operator or getpass.getuser(), confirmed=confirmed)
    except WorkspaceLockHeldError as error:
        click.secho(f"RUN_ACTIVE: {error}", fg="red", err=True)
        sys.exit(_RUNS_EXIT_RUN_ACTIVE)
    except WorkspaceOwnershipError as error:
        click.secho(f"Refused: {error}", fg="red", err=True)
        sys.exit(_RUNS_EXIT_ACTION_REQUIRED)
    if as_json:
        click.echo(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    elif result.ok:
        click.secho(f"{result.status}: {result.message}", fg="green")
    else:
        click.secho(f"Refused ({result.reason_code}): {result.message}", fg="red", err=True)
    if not result.ok:
        sys.exit(_RUNS_EXIT_ACTION_REQUIRED)


@runs_group.command(name="tool-abandon")
@_runs_tool_disposition_options
def runs_tool_abandon(subtask_id, workspace, attempt, legacy, confirmed, operator, as_json) -> None:
    """Abandon a TOOL subtask: it is never executed again and the plan cannot complete past it. Abandoning is not completing, and does not undo the effect."""
    _runs_tool_dispose("abandon", subtask_id, workspace, attempt, legacy, confirmed, operator, as_json)


@runs_group.command(name="tool-retry")
@_runs_tool_disposition_options
def runs_tool_retry(subtask_id, workspace, attempt, legacy, confirmed, operator, as_json) -> None:
    """Re-arm a TOOL subtask as a NEW attempt (N+1). Nothing is executed now: the next resume runs it through the normal policy/approval path with the authority current then."""
    _runs_tool_dispose("retry", subtask_id, workspace, attempt, legacy, confirmed, operator, as_json)
