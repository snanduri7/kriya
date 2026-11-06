/**
 * Every FINISHED run opened from history is a REPLAY of what Kriya recorded: the same KUP projection and the same components as a live result, read from the stored evidence.
 * The line names the run and says what is true of the view: the result is read from stored evidence and opening it re-executes nothing. It makes no claim about whether anything
 * is running (a row stored as in progress may be live, so it carries no replay label), and it says the project-level states (language server, outward lookup, ...) describe the
 * project's configuration today, not the run. It carries the run id only (a fact the view already has); no event is created for the view.
 */
export function ReplayNotice({ runId, status }: { runId: string; status: string | null }) {
  if (status === 'in_progress') return null;
  return (
    <p className="ck-replay muted small" data-testid="replay-notice">
      <strong>Replay of Run <span className="mono">{runId}</span></strong> · the result below is read from the evidence Kriya stored for this run, and opening it re-executes nothing. Language server and outward lookup describe this project today.
    </p>
  );
}
