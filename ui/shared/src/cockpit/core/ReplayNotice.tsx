/**
 * Every run opened from history is a REPLAY of what Kriya recorded: the same KUP projection and the same components as a live result, read from the stored evidence.
 * This line says so in plain words - nothing is executing, nothing is re-run, and no event is created for the view. It carries the run id only (a fact the view already has).
 */
export function ReplayNotice({ runId }: { runId: string }) {
  return (
    <p className="ck-replay muted small" data-testid="replay-notice">
      <strong>Replay of Run <span className="mono">{runId}</span></strong> · read-only view of the evidence Kriya stored for this run. Nothing is running and nothing is re-executed.
    </p>
  );
}
