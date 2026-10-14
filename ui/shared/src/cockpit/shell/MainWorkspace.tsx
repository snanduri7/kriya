import { AvailabilityView, Badge, PROJECT_PAGE, TabBar, useDispatch, useNavigate, useRunContext, useShellSelection, type Resolved } from '../core';
import { ModuleHost } from './ModuleHost';

/** The tab that holds the second-level documents (Plan, Attempts, Context, Evidence, ...). It is a label in this layout, never a module. */
const DETAILS = 'details';

/** Layout only: the run header (from RunContext), the header modules, the tab bar and the active run-scoped document. Overview, Changes and Validation are first-level; everything else is under Details. */
export function MainWorkspace({ header, tabs }: { header: readonly Resolved[]; tabs: readonly Resolved[] }) {
  const dispatch = useDispatch();
  const go = useNavigate();
  const chosen = useShellSelection('centralTab');
  const runId = useShellSelection('runId');
  const run = useRunContext();
  const first = tabs.filter((t) => t.def.placement.group === undefined);
  const deeper = tabs.filter((t) => t.def.placement.group === DETAILS);
  const inDetails = deeper.some((t) => t.def.id === chosen) || (first.length === 0 && deeper.length > 0);
  const activeFirst = first.find((t) => t.def.id === chosen) ?? first[0];
  const activeDeeper = deeper.find((t) => t.def.id === chosen) ?? deeper[0];
  const bar = [...first.map((t) => ({ id: t.def.id, label: t.def.placement.label })), ...(deeper.length ? [{ id: DETAILS, label: 'Details' }] : [])];
  const select = (id: string) => dispatch({ type: 'centralTab', tab: id === DETAILS ? activeDeeper?.def.id ?? '' : id });
  return (
    <main className="ck-center" aria-label="Run workspace">
      {runId === null ? (
        <div className="ck-state">
          <strong>Select a run</strong>
          Choose a run from the list to see what changed and how it was validated. To start new work, go to the Project page.
          <div><button type="button" onClick={() => go(PROJECT_PAGE)}>Go to Project</button></div>
        </div>
      ) : (
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
          {bar.length ? <TabBar label="Run documents" tabs={bar} active={inDetails ? DETAILS : activeFirst?.def.id ?? ''} onSelect={select} /> : null}
          {inDetails && activeDeeper ? (
            <>
              <TabBar label="Details" tabs={deeper.map((t) => ({ id: t.def.id, label: t.def.placement.label }))} active={activeDeeper.def.id} onSelect={(tab) => dispatch({ type: 'centralTab', tab })} />
              <div role="tabpanel"><ModuleHost resolved={activeDeeper} /></div>
            </>
          ) : activeFirst ? <div role="tabpanel"><ModuleHost resolved={activeFirst} /></div> : null}
        </>
      )}
    </main>
  );
}
