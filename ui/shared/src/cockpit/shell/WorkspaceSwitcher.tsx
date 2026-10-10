import { useEffect, useRef, useState } from 'react';
import { useWorkspace } from '../core';
import { OPEN_FOLDER_SHORTCUT, folderLabels } from './WelcomeScreen';

/** The toolbar's workspace control: the open workspace's NAME (the exact path belongs to the Workspace page), with Open Folder, the recent folders and Close. */
export function WorkspaceSwitcher() {
  const ws = useWorkspace();
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return undefined;
    const away = (e: MouseEvent) => { if (root.current && !root.current.contains(e.target as Node)) setOpen(false); };
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', away);
    document.addEventListener('keydown', key);
    return () => { document.removeEventListener('mousedown', away); document.removeEventListener('keydown', key); };
  }, [open]);
  const others = ws.recent.filter((p) => p !== ws.current?.path);
  const labels = folderLabels(ws.current ? [ws.current.path, ...others] : others);
  const name = ws.current ? ws.current.facts.name : null;
  const choose = (action: () => Promise<void>) => { setOpen(false); void action(); };
  return (
    <div className="ck-switcher" ref={root}>
      <button type="button" aria-haspopup="menu" aria-expanded={open} aria-label="Switch workspace" onClick={() => setOpen(!open)} disabled={!ws.ready}>
        {name ?? 'Open Folder…'} <span aria-hidden="true">▾</span>
      </button>
      {open ? (
        <div className="ck-menu" role="menu" aria-label="Workspace menu">
          {ws.canChoose ? <button type="button" role="menuitem" onClick={() => choose(ws.openFolder)}>Open Folder… <span className="muted small">{OPEN_FOLDER_SHORTCUT}</span></button> : null}
          {others.map((path) => (
            <button type="button" role="menuitem" key={path} onClick={() => choose(() => ws.openRecent(path))}>{labels.get(path)}</button>
          ))}
          {ws.current ? <button type="button" role="menuitem" onClick={() => choose(ws.close)}>Close workspace</button> : null}
        </div>
      ) : null}
    </div>
  );
}
