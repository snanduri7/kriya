/** The replay label: names the run, claims only what is true of the view, and is absent for a row that may still be running. */
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ReplayNotice } from './ReplayNotice';

describe('the replay notice', () => {
  it('names the run, says the result is read from stored evidence and that opening it re-executes nothing', () => {
    render(<ReplayNotice runId="1e51ee67" status="success" />);
    const text = screen.getByTestId('replay-notice').textContent ?? '';
    expect(text).toContain('Replay of Run 1e51ee67');
    expect(text).toContain('read from the evidence Kriya stored');
    expect(text).toContain('re-executes nothing');
    expect(text).toContain('describe this project today');
  });

  it('never claims whether anything is running', () => {
    render(<ReplayNotice runId="r" status="failure" />);
    expect(screen.getByTestId('replay-notice').textContent ?? '').not.toMatch(/nothing is running|in progress|live/i);
  });

  it('is absent for a run stored as in progress (it may be live), and present for a failed run', () => {
    const { rerender } = render(<ReplayNotice runId="r" status="in_progress" />);
    expect(screen.queryByTestId('replay-notice')).toBeNull();
    rerender(<ReplayNotice runId="r" status="failure" />);
    expect(screen.getByTestId('replay-notice')).toBeTruthy();
    rerender(<ReplayNotice runId="r" status={null} />);
    expect(screen.getByTestId('replay-notice')).toBeTruthy();
  });
});
