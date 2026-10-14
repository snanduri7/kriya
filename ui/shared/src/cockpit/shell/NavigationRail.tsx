import type { PageDefinition } from '../core';

/** The five destinations, each with a visible text label (an icon alone is never the only cue). Ctrl/Cmd+1..5 follow the order shown. */
export function NavigationRail({ pages, active, onSelect }: { pages: readonly PageDefinition[]; active: string; onSelect(id: string): void }) {
  return (
    <nav className="ck-rail" aria-label="Destinations">
      {pages.map((page, i) => (
        <button key={page.id} type="button" aria-current={page.id === active ? 'page' : undefined} aria-pressed={page.id === active} title={`${page.label} (Ctrl/Cmd+${i + 1})`} aria-label={page.label} onClick={() => onSelect(page.id)}>
          <span className="ck-rail-icon" aria-hidden="true">{page.icon ?? page.label.slice(0, 1)}</span>
          <span className="ck-rail-label" aria-hidden="true">{page.label}</span>
        </button>
      ))}
    </nav>
  );
}
