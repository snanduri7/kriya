import { type KeyboardEvent, type ReactNode } from 'react';

export interface TabSpec<K extends string> { key: K; label: string; badge?: string | null }

export function Tabs<K extends string>({ tabs, active, onChange, label, idPrefix }: { tabs: readonly TabSpec<K>[]; active: K; onChange: (k: K) => void; label: string; idPrefix: string }) {
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const idx = tabs.findIndex((t) => t.key === active);
    let next: number;
    if (e.key === 'ArrowRight') next = (idx + 1) % tabs.length;
    else if (e.key === 'ArrowLeft') next = (idx - 1 + tabs.length) % tabs.length;
    else if (e.key === 'Home') next = 0;
    else if (e.key === 'End') next = tabs.length - 1;
    else return;
    e.preventDefault();
    const tab = tabs[next];
    if (tab) { onChange(tab.key); document.getElementById(`${idPrefix}-tab-${tab.key}`)?.focus(); }
  };
  return (
    <div role="tablist" aria-label={label} className="tabs" onKeyDown={onKeyDown}>
      {tabs.map((t) => (
        <button
          key={t.key}
          id={`${idPrefix}-tab-${t.key}`}
          role="tab"
          type="button"
          aria-selected={t.key === active}
          aria-controls={`${idPrefix}-panel-${t.key}`}
          tabIndex={t.key === active ? 0 : -1}
          className={`tab${t.key === active ? ' active' : ''}`}
          onClick={() => onChange(t.key)}
        >
          {t.label}{t.badge ? <span className="badge">{t.badge}</span> : null}
        </button>
      ))}
    </div>
  );
}

export function TabPanel({ idPrefix, tabKey, active, children }: { idPrefix: string; tabKey: string; active: boolean; children: ReactNode }) {
  return (
    <div role="tabpanel" id={`${idPrefix}-panel-${tabKey}`} aria-labelledby={`${idPrefix}-tab-${tabKey}`} hidden={!active} className="tabpanel">
      {active ? children : null}
    </div>
  );
}
