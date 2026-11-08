import { CopyText } from './CopyText';
import type { RuntimeObservation } from './presentation';

/**
 * RUNTIME VERIFICATION, as observed: the command Kriya's application runtime check ran, the output it printed and its exit status - exactly the backend's recorded, redacted and capped evidence. Output that was cut here or
 * lost by the capture is SAID so, never presented as complete. Evidence for a person to read; it closes no requirement (the backend's requirement truth does that, from a binding).
 */
export function ObservedOutput({ observation }: { observation: RuntimeObservation | null }) {
  if (observation === null) return null;
  const incomplete = observation.stdoutLost ? 'Part of the output was lost when it was captured, so this is not the complete output.' : observation.truncated ? 'The output was long; only the first part is shown here.' : null;
  return (
    <section className="ck-observed" aria-label="Runtime verification" data-testid="observed-output">
      <h4>Runtime verification</h4>
      <div className="ck-observed-row"><span className="muted small">Command</span> <code data-testid="observed-command">{observation.command}</code></div>
      <div className="ck-observed-row"><span className="muted small">Observed output</span>
        {observation.stdout === '' ? <span className="muted" data-testid="observed-empty"> (the program printed nothing)</span> : <pre className="ck-observed-stdout mono" data-testid="observed-stdout">{observation.stdout}</pre>}
      </div>
      <div className="ck-observed-row"><span className="muted small">Exit status</span> <code data-testid="observed-exit">{observation.exitStatus === null ? 'not recorded' : String(observation.exitStatus)}</code>
        {observation.stdout !== '' ? <> <CopyText text={observation.stdout} what="observed output" label="Copy output" /></> : null}</div>
      {incomplete ? <p className="muted small" role="note" data-testid="observed-incomplete">{incomplete}</p> : null}
    </section>
  );
}
