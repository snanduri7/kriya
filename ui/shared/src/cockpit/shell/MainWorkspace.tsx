import { AvailabilityView, Badge, TabBar, useDispatch, useRunContext, useShellSelection, type Resolved } from '../core';
import { ModuleHost } from './ModuleHost';

/** Layout only: the run header (from RunContext), the header modules, the tab bar and the active run-scoped document. */
export function MainWorkspace({ header, tabs }: { header: readonly Resolved[]; tabs: readonly Resolved[] }) {
  const dispatch = useDispatch();
  const chosen = useShellSelection('centralTab');
  const runId = useShellSelection('runId');
  const run = useRunContext();
  const active = tabs.find((t) => t.def.id === chosen) ?? tabs[0];
  return (
    <main className="ck-center" aria-label="Run workspace">
      {runId === null ? <div className="ck-state"><strong>No run selected</strong>Choose a run from the list.</div> : (
        <>
          <AvailabilityView value={run}>
            {(ctx) => (
              <header>
                <h2 style={{ margin: 0, fontSize: 16 }}>{ctx.goal ?? ctx.runId}</h2>
                <div className="muted mono">{ctx.runId} <Badge>{ctx.status === 'in_progress' ? 'Unfinished' : (ctx.status ?? 'status not recorded')}</Badge>{' '}
                  {ctx.milestone.groupId ? <Badge>{`milestone ${(ctx.milestone.index ?? 0) + 1}/${ctx.milestone.total ?? '?'}`}</Badge> : null}</div>
              </header>
            )}
          </AvailabilityView>
          {header.map((m) => <ModuleHost key={m.def.id} resolved={m} />)}
          {tabs.length ? <TabBar label="Run documents" tabs={tabs.map((t) => ({ id: t.def.id, label: t.def.placement.label }))} active={active?.def.id ?? ''} onSelect={(tab) => dispatch({ type: 'centralTab', tab })} /> : null}
          {active ? <div role="tabpanel"><ModuleHost resolved={active} /></div> : null}
        </>
      )}
    </main>
  );
}
