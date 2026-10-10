import { TabBar, useDispatch, useShellSelection, type Resolved } from '../core';
import { ModuleHost } from './ModuleHost';

/** The bottom surface: streams and logs for the current selection. Open/closed and the active tab live in the shared selection. */
export function EvidenceDrawer({ modules }: { modules: readonly Resolved[] }) {
  const dispatch = useDispatch();
  const open = useShellSelection('drawerOpen');
  const chosen = useShellSelection('bottomTab');
  if (!modules.length) return null;
  const active = modules.find((m) => m.def.id === chosen) ?? modules[0]!;
  return (
    <footer className="ck-drawer" aria-label="Evidence drawer">
      <div style={{ display: 'flex', alignItems: 'center' }}>
        <TabBar label="Drawer tabs" tabs={modules.map((m) => ({ id: m.def.id, label: m.def.placement.label }))} active={open ? active.def.id : ''} onSelect={(tab) => dispatch({ type: 'bottomTab', tab })} />
        <button type="button" style={{ marginLeft: 'auto' }} aria-expanded={open} onClick={() => dispatch({ type: 'toggleDrawer' })}>{open ? 'Collapse' : 'Expand'}</button>
      </div>
      {open ? <div className="ck-drawer-body" role="tabpanel"><ModuleHost resolved={active} /></div> : null}
    </footer>
  );
}
