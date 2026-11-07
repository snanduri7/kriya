import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { RunStepper } from './RunStepper';
import type { PhaseStep } from './runPhase';

const steps = (...status: PhaseStep['status'][]): PhaseStep[] => (['planning', 'editing', 'validating', 'finished'] as const).map((id, i) => ({ id, label: ['Planning', 'Implementing', 'Verifying', 'Complete'][i]!, status: status[i]! }));

describe('RunStepper: the horizontal stage tracker', () => {
  it('draws exactly the four authoritative stages in order, with no percentage, count or estimate', () => {
    render(<RunStepper steps={steps('done', 'done', 'current', 'pending')} />);
    const list = screen.getByRole('list', { name: 'Progress' });
    expect(within(list).getAllByRole('listitem').map((li) => li.getAttribute('data-step'))).toEqual(['planning', 'editing', 'validating', 'finished']);
    expect(list.textContent).toMatch(/Planning.*Implementing.*Verifying.*Complete/);
    expect(list.textContent).not.toMatch(/\d+\s*%|\bETA\b|step \d+ of|minutes?|seconds?/i);
  });
  it('done is a check, the current stage is aria-current=step, pending is quiet - and each has spoken status', () => {
    render(<RunStepper steps={steps('done', 'done', 'current', 'pending')} />);
    const item = (id: string) => document.querySelector(`[data-step="${id}"]`) as HTMLElement;
    expect(item('planning')).toHaveAttribute('data-status', 'done');
    expect(item('planning').querySelector('.ck-step-dot')!.textContent).toBe('✓');
    expect(item('validating')).toHaveAttribute('aria-current', 'step');
    expect(item('editing')).not.toHaveAttribute('aria-current');
    expect(item('finished')).toHaveAttribute('data-status', 'pending');
    expect(item('finished').querySelector('.ck-step-dot')!.textContent).toBe('');
    expect(item('planning')).toHaveTextContent('(done)');
    expect(item('validating')).toHaveTextContent('(in progress)');
    expect(item('finished')).toHaveTextContent('(not started)');
    expect(document.querySelectorAll('[aria-current="step"]')).toHaveLength(1);
  });
  it('a run that waits for the person marks only the current stage as waiting', () => {
    render(<RunStepper steps={steps('done', 'current', 'pending', 'pending')} waiting />);
    expect(document.querySelector('[data-step="editing"]')).toHaveAttribute('data-waiting', 'true');
    expect(document.querySelectorAll('[data-waiting]')).toHaveLength(1);
  });
  it('a finished run shows every stage done and none current', () => {
    render(<RunStepper steps={steps('done', 'done', 'done', 'done')} />);
    expect(document.querySelectorAll('[data-status="done"]')).toHaveLength(4);
    expect(document.querySelector('[aria-current="step"]')).toBeNull();
  });
});
