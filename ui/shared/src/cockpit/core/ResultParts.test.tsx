/** The shared result parts: the application result renders only what was recorded, and a very long timeline is bounded without hiding that it is. */
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { ApplicationOutcome } from '../../model/kup';
import { ApplicationResult, HumanTimeline, TIMELINE_SHOWN, TrustList } from './ResultParts';

const exec = (over: Partial<ApplicationOutcome> = {}): ApplicationOutcome => ({ status: 'EXECUTED', succeeded: true, command: 'java -jar target/hello.jar', commands: [], exit_code: 0, timed_out: false, attempt: 1, stdout: { text: 'Hello from Kriya\n', truncated: false }, stderr: { text: '', truncated: false }, ...over }) as ApplicationOutcome;

describe('ApplicationResult', () => {
  it('exit 0 + recorded success: "Ran successfully" with the command and the printed output', () => {
    render(<ApplicationResult app={exec()} />);
    expect(screen.getByText('✓ Ran successfully')).toBeInTheDocument();
    expect(screen.getByLabelText('Application output')).toHaveTextContent('Hello from Kriya');
    expect(screen.getByText('java -jar target/hello.jar')).toBeInTheDocument();
  });
  it('a non-zero exit never reads as success and says the exit code; a timeout says it did not finish', () => {
    const { unmount } = render(<ApplicationResult app={exec({ succeeded: false, exit_code: 1, stdout: { text: '', truncated: false }, stderr: { text: 'boom', truncated: false } })} />);
    expect(screen.queryByText(/Ran successfully/)).toBeNull();
    expect(screen.getByText(/did not run successfully \(exit code 1\)/)).toBeInTheDocument();
    expect(screen.getByLabelText('Application output')).toHaveTextContent('boom');
    unmount();
    render(<ApplicationResult app={exec({ succeeded: false, timed_out: true, exit_code: null })} />);
    expect(screen.getByText(/did not finish in time/)).toBeInTheDocument();
  });
  it('says the output is longer than shown when the recorded output was truncated; shows "(no output)" for none', () => {
    const { unmount } = render(<ApplicationResult app={exec({ stdout: { text: 'abc', truncated: true } })} />);
    expect(screen.getByText(/longer than can be shown/)).toBeInTheDocument();
    unmount();
    render(<ApplicationResult app={exec({ stdout: { text: '', truncated: false } })} />);
    expect(screen.getByLabelText('Application output')).toHaveTextContent('(no output)');
  });
  it('renders nothing for NOT_APPLICABLE or UNKNOWN: no command is ever suggested', () => {
    for (const status of ['NOT_APPLICABLE', 'UNKNOWN'] as const) {
      const { container, unmount } = render(<ApplicationResult app={{ status, reason: 'x' } as ApplicationOutcome} />);
      expect(container).toBeEmptyDOMElement();
      unmount();
    }
  });
  it('is plain text: markup in output is not interpreted', () => {
    render(<ApplicationResult app={exec({ stdout: { text: '<img src=x onerror=alert(1)>', truncated: false } })} />);
    expect(document.querySelector('img')).toBeNull();
  });
});

describe('HumanTimeline', () => {
  const entry = (i: number) => ({ at: i, time: '10:00', text: `step ${i}`, kind: 'info' as const });
  it('shows every line of a normal run and says nothing about hidden ones', () => {
    render(<HumanTimeline entries={Array.from({ length: 8 }, (_, i) => entry(i))} />);
    expect(screen.getAllByRole('listitem')).toHaveLength(8);
    expect(screen.queryByTestId('timeline-more')).toBeNull();
  });
  it('bounds a very long timeline to its first and last lines and says how many are in the technical timeline', () => {
    render(<HumanTimeline entries={Array.from({ length: 500 }, (_, i) => entry(i))} />);
    expect(screen.getAllByRole('listitem')).toHaveLength(TIMELINE_SHOWN);
    expect(screen.getByText('step 0')).toBeInTheDocument();
    expect(screen.getByText('step 499')).toBeInTheDocument();
    expect(screen.queryByText('step 250')).toBeNull();
    expect(screen.getByTestId('timeline-more')).toHaveTextContent(`${500 - TIMELINE_SHOWN} more events are in the technical timeline`);
  });
  it('says so when nothing was recorded', () => {
    render(<HumanTimeline entries={[]} />);
    expect(screen.getByText('Kriya recorded no events for this run.')).toBeInTheDocument();
  });
});

describe('TrustList', () => {
  it('marks recorded facts, problems and unknowns differently and never ticks an unknown', () => {
    render(<TrustList items={[{ category: 'Goal', text: 'Original request recorded', state: 'ok' }, { category: 'Build', text: 'Build failed', state: 'bad' }, { category: 'Final result', text: 'No evidence store was read', state: 'unknown' }]} />);
    const rows = Array.from(document.querySelectorAll('[data-state]'));
    expect(rows.map((r) => [r.getAttribute('data-state'), r.querySelector('dd span')!.textContent])).toEqual([['ok', '✓'], ['bad', '✕'], ['unknown', '•']]);
  });
});
