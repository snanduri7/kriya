import { useLayoutEffect, useRef } from 'react';
import { KriyaLogView, RecordedResults, localClock, shouldStickToBottom, useExecution, useRunActivity, useWorkspaceUi, type DockTab } from '../../core';

type Tab = DockTab;
const TABS: ReadonlyArray<{ id: Tab; label: string }> = [{ id: 'activity', label: 'Activity' }, { id: 'log', label: 'Kriya Log' }, { id: 'build', label: 'Build' }, { id: 'tests', label: 'Tests' }];
const MARK = { info: '•', ok: '✓', bad: '✕', wait: '⚠' } as const;

/**
 * The bottom ACTIVITY dock: what Kriya did, in plain words, from RECORDED facts only (activity.ts), with each line's own recorded time in the person's local clock. It is not a terminal: there is
 * no process stdout/stderr here (no safe, redacted projection of it exists) - a line with no record behind it does not exist. Collapsed, it shows the latest line; it opens by itself while a
 * run is active and stays however the person last set it. It follows new lines only while the person is at the bottom: scrolling up to read older ones is never undone.
 */
export function ActivityDock() {
  const ex = useExecution();
  const ui = useWorkspaceUi();
  const run = ex.run;
  const active = run !== null && run.stage !== 'finished';
  const open = ui.dockOpen ?? active;
  const tab = ui.dockTab;
  const setTab = ui.setDockTab;
  const { entries, records, data } = useRunActivity(run);
  const latest = entries[entries.length - 1] ?? null;
  const list = useRef<HTMLDivElement | null>(null);
  const stick = useRef(true);
  useLayoutEffect(() => {
    const box = list.current;
    if (box && stick.current) box.scrollTop = box.scrollHeight;
  }, [entries.length, open, tab]);
  const toggle = () => ui.setDockOpen(!open);
  return (
    <section className="ck-dock" aria-label="Activity" data-testid="activity-dock" data-open={open}>
      <div className="ck-dock-bar">
        <button type="button" className="ck-dock-toggle" aria-expanded={open} onClick={toggle}>
          <span aria-hidden="true">{open ? '▾' : '▸'}</span> <strong>ACTIVITY</strong>
        </button>
        {!open ? <span className="ck-dock-latest muted small" data-testid="activity-latest">{latest ? `${localClock(latest.at)} ${latest.text}`.trim() : run ? 'Waiting for the first record…' : 'Nothing running. What Kriya does in your next run appears here.'}</span> : (
          <div role="tablist" aria-label="Activity views" className="ck-dock-tabs">
            {TABS.map((t) => <button key={t.id} type="button" role="tab" aria-selected={tab === t.id} className={tab === t.id ? 'ck-primary' : ''} onClick={() => { setTab(t.id); if (t.id !== 'activity') ui.setDockOpen(true); }}>{t.label}</button>)}
          </div>
        )}
      </div>
      {open ? (
        <div className="ck-dock-body" ref={list} data-testid="activity-scroll" onScroll={(e) => { stick.current = shouldStickToBottom(e.currentTarget); }}>
          {!run ? <p className="muted" role="status">Nothing is running. What Kriya does in your next run appears here, line by line, from its own records.</p> : null}
          {run && tab === 'activity' ? (entries.length === 0
            ? <p className="muted" role="status">{records && records.state === 'loading' ? 'Reading what Kriya recorded…' : 'Nothing has been recorded yet.'}</p>
            : <ol className="ck-human-timeline" aria-label="What happened" data-testid="activity-list">{entries.map((e) => <li key={e.key} data-tone={e.tone} title={`Recorded as ${e.source}`}><span className="mono small muted">{localClock(e.at)}</span> <span aria-hidden="true">{MARK[e.tone]}</span> {e.text}</li>)}</ol>) : null}
          {run && tab === 'log' ? <KriyaLogView runId={run.launchId} live={active} /> : null}
          {run && (tab === 'build' || tab === 'tests') ? <RecordedResults records={data} family={tab} loading={records?.state === 'loading'} /> : null}
          {run && tab !== 'log' && records && records.state !== 'loading' && data === null ? <p className="muted small" role="note">The records could not be read here{records.state === 'error' ? ` (${records.code})` : ''}. Use View Details.</p> : null}
        </div>
      ) : null}
    </section>
  );
}
