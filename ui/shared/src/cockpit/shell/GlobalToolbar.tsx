import { useEffect, useState } from 'react';
import { Badge, ProviderBanner, useProvider } from '../core';

export interface ToolbarToggles { secondary: boolean; inspector: boolean; drawer: boolean }

export function GlobalToolbar({ showSecondaryToggle, open, onToggle }: { showSecondaryToggle: boolean; open: ToolbarToggles; onToggle(which: keyof ToolbarToggles): void }) {
  const provider = useProvider();
  const [where, setWhere] = useState<{ config: string | null; workspace: string | null }>({ config: null, workspace: null });
  useEffect(() => {
    let live = true;
    void Promise.all([provider.getSetting('configDirectory'), provider.getSetting('workspacePath')]).then(([config, workspace]) => { if (live) setWhere({ config: config ?? null, workspace: workspace ?? null }); });
    return () => { live = false; };
  }, [provider]);
  return (
    <div>
      <ProviderBanner kind={provider.kind} />
      <header className="ck-toolbar" role="banner">
        <span className="ck-mark">Kriya</span>
        <span className="muted optional" aria-label="Configuration directory">{where.config ?? 'default configuration'}</span>
        <span className="muted optional" aria-label="Workspace">{where.workspace ?? 'no workspace selected'}</span>
        <span className="grow" />
        {showSecondaryToggle ? <button type="button" aria-pressed={open.secondary} onClick={() => onToggle('secondary')}>Runs pane</button> : null}
        <button type="button" aria-pressed={open.inspector} onClick={() => onToggle('inspector')}>Inspector</button>
        <button type="button" aria-pressed={open.drawer} onClick={() => onToggle('drawer')}>Drawer</button>
        {provider.kind === 'fixture' ? <Badge tone="warn">fixture</Badge> : null}
      </header>
    </div>
  );
}
