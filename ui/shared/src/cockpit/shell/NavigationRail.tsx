import type { PageDefinition } from '../core';

export function NavigationRail({ pages, active, onSelect }: { pages: readonly PageDefinition[]; active: string; onSelect(id: string): void }) {
  return (
    <nav className="ck-rail" aria-label="Destinations">
      {pages.map((page, i) => (
        <button key={page.id} type="button" aria-current={page.id === active ? 'page' : undefined} aria-pressed={page.id === active} title={`${page.label} (Ctrl/Cmd+${i + 1})`} aria-label={page.label} onClick={() => onSelect(page.id)}>
          <span aria-hidden="true">{page.icon ?? page.label.slice(0, 1)}</span>
        </button>
      ))}
    </nav>
  );
}
