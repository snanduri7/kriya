import { Badge, ProviderBanner, useProvider } from '../core';
import { WorkspaceSwitcher } from './WorkspaceSwitcher';
import { WorkspaceNotice } from './WelcomeScreen';
import { ActiveRunBanner } from './ActiveRunBanner';

export interface ToolbarToggles { secondary: boolean; inspector: boolean; drawer: boolean }

/** The global toolbar. It names the open workspace (never its path: that belongs to the Workspace page) and offers the same Open Folder action as the menu. */
export function GlobalToolbar({ showSecondaryToggle, open, onToggle, bare = false }: { showSecondaryToggle: boolean; open: ToolbarToggles; onToggle(which: keyof ToolbarToggles): void; bare?: boolean }) {
  const provider = useProvider();
  return (
    <div>
      <ProviderBanner kind={provider.kind} />
      <header className="ck-toolbar" role="banner">
        <span className="ck-mark">Kriya</span>
        <WorkspaceSwitcher />
        <span className="grow" />
        {!bare && showSecondaryToggle ? <button type="button" aria-pressed={open.secondary} onClick={() => onToggle('secondary')}>Runs pane</button> : null}
        {!bare ? <button type="button" aria-pressed={open.inspector} onClick={() => onToggle('inspector')}>Inspector</button> : null}
        {!bare ? <button type="button" aria-pressed={open.drawer} onClick={() => onToggle('drawer')}>Drawer</button> : null}
        {provider.kind === 'fixture' ? <Badge tone="warn">fixture</Badge> : null}
      </header>
      {bare ? null : <WorkspaceNotice />}
      {bare ? null : <ActiveRunBanner />}
    </div>
  );
}
