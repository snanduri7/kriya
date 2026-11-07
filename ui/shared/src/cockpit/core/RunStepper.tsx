import type { PhaseStep } from './runPhase';

const SPOKEN = { done: 'done', current: 'in progress', pending: 'not started' } as const;

/**
 * The horizontal stage tracker of an active run: Planning - Implementing - Verifying - Complete, nothing else. The four steps ARE the authoritative phases ``runPhase`` derives from Kriya's
 * records; this only draws them (no percentage, no estimate, no confidence). DONE is a filled green circle with a check, CURRENT the accent ring with the bold label (amber when the run waits for
 * the person), PENDING quiet. The list keeps ``aria-current="step"`` and spoken status for assistive technology.
 */
export function RunStepper({ steps, waiting = false }: { steps: readonly PhaseStep[]; waiting?: boolean }) {
  return (
    <ol className="ck-stepper" aria-label="Progress" data-testid="run-stepper">
      {steps.map((s) => (
        <li key={s.id} data-step={s.id} data-status={s.status} data-waiting={s.status === 'current' && waiting ? 'true' : undefined} aria-current={s.status === 'current' ? 'step' : undefined}>
          <span className="ck-step-dot" aria-hidden="true">{s.status === 'done' ? '✓' : ''}</span>
          <span className="ck-step-label">{s.label}</span>
          <span className="sr-only"> ({SPOKEN[s.status]})</span>
        </li>
      ))}
    </ol>
  );
}
