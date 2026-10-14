import { Badge, ProviderBanner, useProvider } from '../core';
import { WorkspaceSwitcher } from './WorkspaceSwitcher';
import { WorkspaceNotice } from './WelcomeScreen';
import { ActiveRunBanner } from './ActiveRunBanner';

export interface ToolbarToggles { secondary: boolean; inspector: boolean }

/** The global toolbar. It names the open workspace (never its path: that belongs to the Workspace page) and offers the same Open Folder action as the menu. */
export function GlobalToolbar({ showSecondaryToggle, showInspectorToggle, open, onToggle, bare = false, runStrip = true }: { showSecondaryToggle: boolean; showInspectorToggle: boolean; open: ToolbarToggles; onToggle(which: keyof ToolbarToggles): void; bare?: boolean; /** The compact active-run strip (hidden on the Project page, where the run is the page). */ runStrip?: boolean }) {
  const provider = useProvider();
  return (
    <div>
      <ProviderBanner kind={provider.kind} />
      <header className="ck-toolbar" role="banner">
        <span className="ck-mark">Kriya</span>
        <WorkspaceSwitcher />
        <span className="grow" />
        {!bare && showSecondaryToggle ? <button type="button" aria-pressed={open.secondary} onClick={() => onToggle('secondary')}>History</button> : null}
        {!bare && showInspectorToggle ? <button type="button" aria-pressed={open.inspector} onClick={() => onToggle('inspector')}>Technical timeline</button> : null}
        {provider.kind === 'fixture' ? <Badge tone="warn">fixture</Badge> : null}
      </header>
      {bare ? null : <WorkspaceNotice />}
      {bare || !runStrip ? null : <ActiveRunBanner />}
    </div>
  );
}
